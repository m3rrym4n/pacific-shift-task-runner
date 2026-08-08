import subprocess

import pytest

from codex_runner import configure_mcp


def test_empty_configuration_registers_no_servers(monkeypatch):
    calls = []
    monkeypatch.delenv("CODEX_RUNNER_MCP_SERVERS", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    configure_mcp.main()

    assert calls == []


def test_json_configuration_registers_each_server(monkeypatch):
    calls = []
    monkeypatch.setenv(
        "CODEX_RUNNER_MCP_SERVERS",
        '{"ff-mcp":"http://ff:7004/mcp","nfl-mcp":"http://nfl:9000/mcp"}',
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    configure_mcp.main()

    assert calls == [
        ((["codex", "mcp", "add", "ff-mcp", "--url", "http://ff:7004/mcp"],), {"check": True}),
        ((["codex", "mcp", "add", "nfl-mcp", "--url", "http://nfl:9000/mcp"],), {"check": True}),
    ]


@pytest.mark.parametrize(
    "raw",
    ["not-json", "[]", '{"":"http://server/mcp"}', '{"server":""}', '{"server":3}'],
)
def test_invalid_configuration_fails_startup(raw):
    with pytest.raises(ValueError, match="CODEX_RUNNER_MCP_SERVERS"):
        configure_mcp.configured_servers(raw)
