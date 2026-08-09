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
