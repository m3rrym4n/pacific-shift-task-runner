## Per-container configuration

The runner accepts an optional `model` field on `/execute` and `/resume` requests. An explicit
request value takes precedence over the container-level `CODEX_RUNNER_MODEL` default.

At startup, `CODEX_RUNNER_MCP_SERVERS` may contain a JSON object mapping MCP server names to URLs:

```json
{"ff-mcp":"http://ff-mcp:7004/mcp","nfl-mcp":"http://nfl-mcp:9000/mcp"}
```

Each configured server is registered with `codex mcp add`. If the variable is unset or empty, no
extra MCP servers are registered. Invalid configuration fails startup instead of silently running
with missing integrations.

The orchestrator also supplies `TASK_RUNNER_GIT_HOST` for every task and
`TASK_RUNNER_GIT_HOST_BASE_URL` for Forgejo tasks. Before Codex starts, the entrypoint uses the
matching `GITHUB_TOKEN` or `FORGEJO_TOKEN` to export a host-scoped Git `Authorization`
extraheader. If the matching token is absent, Git remains unconfigured so public clones continue
to work normally.
