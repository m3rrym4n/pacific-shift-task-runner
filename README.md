# Pacific Shift Task Runner

FastAPI/SQLite orchestrator that dispatches GitHub issues to registry-configured runner HTTP shims and exposes four MCP tools.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `TASK_RUNNER_RUNNERS` | `{}` | JSON mapping runner names to internal URLs, e.g. `{"codex":"http://192.168.1.68:7000"}` |
| `TASK_RUNNER_SCHEDULED_TASKS` | `[]` | JSON array of scheduled issue dispatches |
| `TASK_RUNNER_OPS_IMAGE_CHECKS` | `[]` | JSON array of scheduled operational image version-drift checks and rebuild jobs |
| `TASK_RUNNER_REPOS` | `[]` | JSON array of onboarded repository and dev/main deploy-target objects |
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
| `TASK_RUNNER_SOURCE_SHA` | `unknown` | Source revision baked into the Task Runner image and used in Ops Images tags |

The MCP Streamable HTTP endpoint is `/mcp/`; the health endpoint is `/`.

### Repository registry

Every dispatched repository must appear in `TASK_RUNNER_REPOS`. Each entry
selects one configured runner and records both its automatically deployed `dev`
target and human-promoted `main` target. Targets require `container`, `volume`,
and a positive integer `port`. Optional `health_path` and `expected_content`
values override the reusable workflow's generic HTTP check. `health_path`
defaults to `/` in that workflow when omitted.

The live four-repository configuration is maintained in
[`deploy/repos.json`](deploy/repos.json). Set the environment variable from the
file when starting the service, for example:

```bash
TASK_RUNNER_REPOS="$(tr -d '\n' < deploy/repos.json)"
export TASK_RUNNER_REPOS
```

`GET /api/repos` exposes the parsed, validated values to internal pipeline and
dashboard consumers. Dispatch rejects missing repositories and runner
mismatches before creating a task row. `pacific-shift-mcp-proxy` is deliberately
absent because its Home Assistant add-on deployment does not use this container
target model.

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

Ops Image checks use the same scheduler and the same per-runner queue as normal
issue dispatches. They call the configured runner's version endpoint and, when
drift is detected, create a normal task row tied to a fixed trace issue. The
queued internal job builds, pushes, prunes, deploys, and verifies the rebuilt
Codex runner image. The fixed trace issue preserves the "written issue behind
every action" rule without creating a new GitHub issue for every maintenance
cycle.

Configure Ops Image checks with `TASK_RUNNER_OPS_IMAGE_CHECKS`:

```json
[
  {
    "name": "daily-codex-runner-rebuild-check",
    "runner": "codex",
    "repo": "m3rrym4n/pacific-shift-task-runner",
    "issue_number": 35,
    "registry": "zot.lan:5000",
    "repository": "codex-runner",
    "stop_container": "codex-runner",
    "start_container": "codex-runner",
    "auth_volume": "pacific-shift-codex-runner-auth",
    "buildkit_addr": "unix:///run/buildkit/buildkitd.sock",
    "source_sha": "abc1234",
    "keep_tags": 2,
    "insecure_tls": false,
    "interval": "1d"
  }
]
```

The Codex runner exposes `GET /codex/version`, returning installed version,
latest npm version, and a `drift_detected` boolean. A drift result enqueues an
internal rebuild job behind any active `codex` work. The job invokes `buildctl`
through the mounted BuildKit socket, builds `codex_runner` with
`CODEX_VERSION=<target>`, tags the image as
`<registry>/<repository>:<codex-version>-<repo-short-sha>`, pushes it to Zot,
runs `scripts/prune_zot_image_tags.py` to keep current plus N-1, calls
Dockhand's `deploy_container_swap`, and verifies that
`pacific-shift-codex-runner-auth` is still mounted after the swap.

The Task Runner container must mount the host BuildKit socket directory at the
same in-container path used by the CrateSpy runner:

```yaml
group_add:
  - "0"
volumes:
  - /DATA/AppData/buildkit/socket:/run/buildkit
```

The BuildKit socket is group-readable by GID `0`; `group_add` lets the
non-root Task Runner process open the socket without changing the container's
primary user.

An example compose deployment is provided in `deploy/docker-compose.yml`.

## Runner contract

Required endpoints are `POST /execute`, `POST /resume`, `GET /status/{execution_id}`, and `GET /result/{execution_id}`. `POST /resume` accepts the original execution request plus a persisted `session_id`; the Codex runner invokes `codex exec resume` and logs an explicit marker before falling back to a fresh dispatch if resume fails. On timeout the orchestrator also attempts `DELETE /execute/{execution_id}`. Runners should implement that optional endpoint to guarantee remote process termination; otherwise the task is still recorded as `timeout`, with the failed cancellation noted.

## Runner queues

`run_task` places every issue dispatch into an in-memory FIFO queue for the
selected runner and returns a receipt with `task_id`, `status`, `position`,
`queue_length`, and `runner`. Idle runners start the new task immediately with
position `0`; busy runners keep later tasks queued until earlier work finishes.
If the active task for a runner fails, times out, or raises during processing,
that runner's queue halts and leaves pending tasks queued for human inspection.
If the runner instead reports `quota_exceeded` from a structured rate-limit
event with a session ID and ISO 8601 `resets_at` timestamp, the interrupted task
returns to the head of the queue and the queue enters a distinct quota halt.
Receipts for work added during that halt include `resumes_at`. At that time the
same task row resumes its Codex session before later pending work starts. Quota
responses without a usable structured reset timestamp remain generic halts.
Phrasing-only quota detections deliberately do not auto-resume: Codex's relative
duration text is neither ISO-compatible nor sufficiently reliable to schedule
unattended work.
Queues are independent per runner and are not persisted across restarts.
Use the `clear_runner_halt` tool to clear a halt for one runner and resume its
remaining pending items without retrying the failed item. Use
`cancel_queued_task` to remove and mark one still-pending item as `cancelled`;
active tasks must instead use the runner shim's execution-cancellation endpoint.

## Docker

```bash
docker build \
  --build-arg "TASK_RUNNER_SOURCE_SHA=$(git rev-parse --short=7 HEAD)" \
  -t pacific-shift-task-runner:latest .

docker stop pacific-shift-task-runner
docker rm pacific-shift-task-runner

docker run -d \
  --name pacific-shift-task-runner \
  --restart unless-stopped \
  --group-add 0 \
  -p 6002:6002 \
  -v pacific-shift-task-runner-data:/data \
  -v /DATA/AppData/buildkit/socket:/run/buildkit \
  -e 'TASK_RUNNER_RUNNERS={"codex":"http://192.168.1.68:7000"}' \
  -e 'TASK_RUNNER_SCHEDULED_TASKS=[]' \
  -e 'TASK_RUNNER_OPS_IMAGE_CHECKS=[]' \
  -e "TASK_RUNNER_REPOS=$(tr -d '\n' < deploy/repos.json)" \
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

## CI/CD

The manually dispatched `.github/workflows/dev-build-deploy.yml` calls the
reusable workflow in `pacific-shift-ci`. It builds through the shared BuildKit
daemon, pushes immutable and rolling development tags to Zot, and replaces the
existing `pacific-shift-task-runner` container through Dockhand. Deployment
uses the running container's inspected configuration as its template and
changes only the image. Generic running/image/HTTP verification and automatic
rollback are supplied by the shared workflow.

The repository requires a self-hosted runner labeled `zimaos` and
`pacific-shift-task-runner`, plus `DOCKHAND_URL` and `DOCKHAND_TOKEN` Actions secrets, before
the workflow can be dispatched. Runner and token provisioning is managed
separately from the reusable workflow.

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
