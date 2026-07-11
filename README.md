# Pacific Shift Task Runner

FastAPI/SQLite orchestrator that dispatches GitHub issues to registry-configured runner HTTP shims and exposes four MCP tools.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `TASK_RUNNER_RUNNERS` | `{}` | JSON mapping runner names to internal URLs, e.g. `{"codex":"http://192.168.1.68:7000"}` |
| `TASK_RUNNER_SCHEDULED_TASKS` | `[]` | JSON array of scheduled issue dispatches |
| `TASK_RUNNER_OPS_IMAGE_CHECKS` | `[]` | JSON array of scheduled operational image version-drift checks |
| `TASK_RUNNER_DATABASE` | `/data/tasks.db` | SQLite database path |
| `TASK_RUNNER_TIMEOUT_SECONDS` | `600` | Hard orchestration timeout |
| `TASK_RUNNER_OUTPUT_CAP_BYTES` | `1000000` | Maximum retained runner log size |
| `TASK_RUNNER_POLL_INTERVAL_SECONDS` | `2` | Runner status polling interval |
| `GITHUB_TOKEN` | unset | Optional token for private repositories or higher API limits |
| `TASK_RUNNER_DOCKHAND_URL` | unset | Dockhand REST API base URL for internal container deploy operations |
| `TASK_RUNNER_DOCKHAND_TOKEN` | unset | Dedicated Task Runner Dockhand API token (`dh_...`); do not reuse `dockhand-mcp` credentials |
| `TASK_RUNNER_DOCKHAND_ENV` | unset | Optional Dockhand environment ID for container deploy operations |
| `TASK_RUNNER_DOCKHAND_VERIFY_TIMEOUT_SECONDS` | `60` | Maximum time to wait for a started container to verify as running or healthy |
| `TASK_RUNNER_DOCKHAND_VERIFY_INTERVAL_SECONDS` | `2` | Poll interval while verifying a started container |

The MCP Streamable HTTP endpoint is `/mcp/`; the health endpoint is `/`.

Dockhand configuration is an internal Task Runner capability for Ops Images
deploy steps. It is not exposed as an MCP tool. The token must be supplied at
runtime through `TASK_RUNNER_DOCKHAND_TOKEN` and should be generated under a
dedicated Task Runner account.

### Scheduled tasks

Issue scheduled tasks reuse the same dispatch path as the `run_task` MCP tool. When a
configured interval fires, Task Runner creates a normal task row for the target
repository issue and runner. The fire is visible in container logs and in
`list_tasks`.

Configure schedules with `TASK_RUNNER_SCHEDULED_TASKS`:

```json
[
  {
    "name": "daily-codex-health-check",
    "repo": "m3rrym4n/pacific-shift-task-runner",
    "issue_number": 15,
    "runner": "codex",
    "interval": "1d"
  }
]
```

`interval` accepts a positive number of seconds or a string with one of these
suffixes:

| Suffix | Meaning | Example |
|---|---:|---|
| `s` | seconds | `120s` |
| `m` | minutes | `2m` |
| `h` | hours | `6h` |
| `d` | days | `1d` |

To add a scheduled job, add an object to `TASK_RUNNER_SCHEDULED_TASKS` and
restart the container. To remove a scheduled job, remove its object from the
array and restart the container. The `runner` value must match a key in
`TASK_RUNNER_RUNNERS`.

Ops Image checks use the same scheduler but do not create normal issue-dispatch
tasks. They call the configured runner's version endpoint and, when drift is
detected, trigger a dedicated GitHub Actions `workflow_dispatch`.

Configure Ops Image checks with `TASK_RUNNER_OPS_IMAGE_CHECKS`:

```json
[
  {
    "name": "daily-codex-runner-rebuild-check",
    "runner": "codex",
    "workflow_repo": "m3rrym4n/pacific-shift-task-runner",
    "workflow_id": "ops-codex-runner-rebuild.yml",
    "ref": "main",
    "interval": "1d"
  }
]
```

The Codex runner exposes `GET /codex/version`, returning installed version,
latest npm version, and a `drift_detected` boolean. A drift result triggers the
Ops Images workflow with the installed and target versions as inputs. The
workflow builds `codex_runner` with `CODEX_VERSION=<target>`, tags the image as
`<codex-version>-<repo-short-sha>`, pushes it to Zot, prunes the Zot repository
to current plus N-1, and reports the ready image tag. It deliberately does not
stop or start the running `codex-runner` container.

The workflow expects these GitHub repository variables/secrets:

| Name | Type | Purpose |
|---|---|---|
| `OPS_IMAGES_REGISTRY` | variable | Zot registry host, without scheme, for Docker push/login |
| `OPS_IMAGES_CODEX_RUNNER_REPOSITORY` | variable | Zot repository name; defaults to `codex-runner` in the workflow |
| `ZOT_USERNAME` | secret | Zot username for Docker login |
| `ZOT_PASSWORD` | secret | Zot password for Docker login |
| `ZOT_TOKEN` | secret | Bearer token used by the retention helper against Zot's registry API |

## Runner contract

Required endpoints are `POST /execute`, `GET /status/{execution_id}`, and `GET /result/{execution_id}`. On timeout the orchestrator also attempts `DELETE /execute/{execution_id}`. Runners should implement that optional endpoint to guarantee remote process termination; otherwise the task is still recorded as `timeout`, with the failed cancellation noted.

## Docker

```bash
docker build -t pacific-shift-task-runner:latest .

docker stop pacific-shift-task-runner
docker rm pacific-shift-task-runner

docker run -d \
  --name pacific-shift-task-runner \
  --restart unless-stopped \
  -p 6002:6002 \
  -v pacific-shift-task-runner-data:/data \
  -e 'TASK_RUNNER_RUNNERS={"codex":"http://192.168.1.68:7000"}' \
  -e 'TASK_RUNNER_SCHEDULED_TASKS=[]' \
  -e 'GITHUB_TOKEN=<redacted>' \
  pacific-shift-task-runner:latest
```

Supply `GITHUB_TOKEN` at runtime; do not store the token in the repository.

Run tests in Docker:

```bash
docker build -t pacific-shift-task-runner:test -f Dockerfile.test .
docker run --rm pacific-shift-task-runner:test
```

Verified via automated end-to-end dispatch.

### Dedicated Codex runner

Build and run the non-interactive runner separately from any interactive Codex container. Its Codex authentication is stored in a named volume.

```bash
docker build -t pacific-shift-codex-runner:latest codex_runner
docker volume create pacific-shift-codex-runner-auth

docker run --rm -it \
  -v pacific-shift-codex-runner-auth:/home/codex/.codex \
  pacific-shift-codex-runner:latest codex login --device-auth

docker run -d \
  --name codex-runner \
  --restart unless-stopped \
  --privileged \
  --group-add "$(stat -c '%g' /var/run/docker.sock)" \
  -p 7000:7000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v pacific-shift-codex-runner-auth:/home/codex/.codex \
  -e 'GITHUB_TOKEN=<redacted>' \
  pacific-shift-codex-runner:latest

docker exec codex-runner docker version
docker exec codex-runner docker ps
curl http://localhost:7000/codex/version
```

Privileged mode allows Codex's own `workspace-write` sandbox to create and
configure its nested Linux namespace. The host Docker socket and its group ID
give the non-root `codex` user access to the host daemon; the image contains
the Docker CLI and Buildx plugin, but no Docker daemon. Supply `GITHUB_TOKEN`
at runtime so the dispatched agent can clone, push, and open its PR. Test the runner image with `docker build -t
pacific-shift-codex-runner:test -f codex_runner/Dockerfile.test codex_runner &&
docker run --rm pacific-shift-codex-runner:test`.
