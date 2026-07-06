# Pacific Shift Task Runner

FastAPI/SQLite orchestrator that dispatches GitHub issues to registry-configured runner HTTP shims and exposes four MCP tools.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `TASK_RUNNER_RUNNERS` | `{}` | JSON mapping runner names to internal URLs, e.g. `{"codex":"http://192.168.1.68:7000"}` |
| `TASK_RUNNER_DATABASE` | `/data/tasks.db` | SQLite database path |
| `TASK_RUNNER_TIMEOUT_SECONDS` | `600` | Hard orchestration timeout |
| `TASK_RUNNER_OUTPUT_CAP_BYTES` | `1000000` | Maximum retained runner log size |
| `TASK_RUNNER_POLL_INTERVAL_SECONDS` | `2` | Runner status polling interval |
| `GITHUB_TOKEN` | unset | Optional token for private repositories or higher API limits |

The MCP Streamable HTTP endpoint is `/mcp/`; the health endpoint is `/`.

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
  -e 'GITHUB_TOKEN=<redacted>' \
  pacific-shift-task-runner:latest
```

Supply `GITHUB_TOKEN` at runtime; do not store the token in the repository.

Run tests in Docker:

```bash
docker build -t pacific-shift-task-runner:test -f Dockerfile.test .
docker run --rm pacific-shift-task-runner:test
```

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
  --security-opt seccomp=unconfined \
  -p 7000:7000 \
  -v pacific-shift-codex-runner-auth:/home/codex/.codex \
  -e 'GITHUB_TOKEN=<redacted>' \
  pacific-shift-codex-runner:latest
```

The seccomp option allows Codex's own `workspace-write` sandbox to create its
Linux namespace inside the dedicated container. Supply `GITHUB_TOKEN` at
runtime so the dispatched agent can clone, push, and open its PR. Test the
runner image with `docker build -t pacific-shift-codex-runner:test -f
codex_runner/Dockerfile.test codex_runner && docker run --rm
pacific-shift-codex-runner:test`.
