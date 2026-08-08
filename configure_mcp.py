import json
import os
import subprocess


def configured_servers(raw: str | None = None) -> dict[str, str]:
    raw = os.getenv("CODEX_RUNNER_MCP_SERVERS", "") if raw is None else raw
    if not raw.strip():
        return {}

    try:
        servers = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("CODEX_RUNNER_MCP_SERVERS must be valid JSON") from exc

    if not isinstance(servers, dict) or not all(
        isinstance(name, str)
        and name.strip()
        and isinstance(url, str)
        and url.strip()
        for name, url in servers.items()
    ):
        raise ValueError(
            "CODEX_RUNNER_MCP_SERVERS must be a JSON object of non-empty name-to-URL strings"
        )
    return servers


def register_servers(servers: dict[str, str]) -> None:
    for name, url in servers.items():
        subprocess.run(["codex", "mcp", "add", name, "--url", url], check=True)


def main() -> None:
    register_servers(configured_servers())


if __name__ == "__main__":
    main()
