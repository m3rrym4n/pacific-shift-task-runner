# Pacific Shift Task Runner

FastAPI/SQLite orchestrator that dispatches GitHub issues to registry-configured runner HTTP shims and exposes four MCP tools.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `TASK_RUNNER_RUNNERS` | `{}` | JSON mapping runner names to internal URLs, e.g. `{"codex":"http://codex-runner:7000"}` |
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
docker run --rm -p 6002:6002 -v pacific-shift-task-runner-data:/data \
  -e 'TASK_RUNNER_RUNNERS={"codex":"http://codex-runner:7000"}' \
  pacific-shift-task-runner:latest
```

Run tests in Docker:

```bash
docker build -t pacific-shift-task-runner:test -f Dockerfile.test .
docker run --rm pacific-shift-task-runner:test
```
