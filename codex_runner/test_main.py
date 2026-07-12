import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from codex_runner import main


def test_execute_lifecycle_and_result(monkeypatch, tmp_path):
    script = tmp_path / "codex"
    script.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"structured report\"}}'\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    main.store = main.ExecutionStore(str(tmp_path))

    with TestClient(main.app) as client:
        response = client.post("/execute", json={"repo": "owner/repo", "issue_number": 3, "prompt": "do it"})
        assert response.status_code == 202
        execution_id = response.json()["execution_id"]
        for _ in range(100):
            if client.get(f"/status/{execution_id}").json()["status"] != "running":
                break
            asyncio.run(asyncio.sleep(0.01))

        result = client.get(f"/result/{execution_id}").json()
        assert result["status"] == "completed"
        assert result["result"] == "structured report"
        assert result["exit_code"] == 0
        assert not any(Path(tmp_path).glob("codex-*-*"))


def test_unknown_execution_returns_404():
    main.store = main.ExecutionStore()
    with TestClient(main.app) as client:
        assert client.get("/status/missing").status_code == 404


def test_repo_validation():
    with TestClient(main.app) as client:
        assert client.post("/execute", json={"repo": "invalid", "prompt": "x"}).status_code == 422


def test_runner_prompt_requires_agent_owned_clone_and_github_workflow():
    request = main.ExecuteRequest(repo="owner/repo", issue_number=3, prompt="issue prompt")

    prompt = main._runner_prompt(request)

    assert "clone that repository" in prompt
    assert "use `git` and `gh`" in prompt
    assert prompt.endswith("issue prompt\n")


def test_version_parser_accepts_codex_cli_output():
    assert main._parse_version("codex-cli 0.142.5") == "0.142.5"
    assert main._parse_version("0.144.1\n") == "0.144.1"


def test_codex_version_endpoint_reports_drift(monkeypatch):
    monkeypatch.setattr(main, "get_installed_codex_version", lambda: "0.142.5")
    monkeypatch.setattr(main, "get_latest_codex_version", lambda: "0.144.1")

    with TestClient(main.app) as client:
        response = client.get("/codex/version")

    assert response.status_code == 200
    assert response.json() == {
        "installed": "0.142.5",
        "latest": "0.144.1",
        "drift_detected": True,
    }


def test_detect_quota_exhaustion_via_structured_rate_limits_event():
    output = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"agent_message","text":"partial work"}}',
            '{"type":"token_count","rate_limits":{"primary":{"used_percent":100,"resets_at":"2026-07-11T15:42:00-07:00"},"secondary":{"used_percent":41}}}',
        ]
    )

    quota = main._detect_quota_exhaustion(output)

    assert quota is not None
    assert quota.resets_at == "2026-07-11T15:42:00-07:00"
    assert quota.structured is True


def test_detect_quota_exhaustion_via_phrasing_fallback_when_no_structured_event():
    output = "You've hit your usage limit. Try again in 3h 42m"

    quota = main._detect_quota_exhaustion(output)

    assert quota is not None
    assert quota.resets_at == "3h 42m"
    assert quota.structured is False


def test_session_id_comes_from_real_thread_started_jsonl_shape():
    output = '\n'.join([
        '{"type":"thread.started","thread_id":"019f5439-3c97-73e0-9265-3c0ed42e9c63"}',
        '{"type":"turn.started"}',
    ])

    assert main._session_id(output) == "019f5439-3c97-73e0-9265-3c0ed42e9c63"


def test_detect_quota_exhaustion_returns_none_for_genuine_output():
    output = '{"type":"item.completed","item":{"type":"agent_message","text":"all good"}}'

    assert main._detect_quota_exhaustion(output) is None


def test_execute_lifecycle_reports_quota_exceeded_via_structured_event(monkeypatch, tmp_path):
    script = tmp_path / "codex"
    script.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"session-structured\"}'\n"
        "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"partial\"}}'\n"
        "printf '%s\\n' '{\"type\":\"token_count\",\"rate_limits\":{\"primary\":{\"used_percent\":100,\"resets_at\":\"2026-07-11T15:42:00-07:00\"}}}'\n"
        "exit 1\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    main.store = main.ExecutionStore(str(tmp_path))

    with TestClient(main.app) as client:
        response = client.post("/execute", json={"repo": "owner/repo", "issue_number": 3, "prompt": "do it"})
        execution_id = response.json()["execution_id"]
        for _ in range(100):
            if client.get(f"/status/{execution_id}").json()["status"] != "running":
                break
            asyncio.run(asyncio.sleep(0.01))

        result = client.get(f"/result/{execution_id}").json()
        assert result["status"] == "quota_exceeded"
        assert result["resets_at"] == "2026-07-11T15:42:00-07:00"
        assert result["session_id"] == "session-structured"
        assert result["quota_auto_resume"] is True
        assert "usage limit" in result["error"].lower()


def test_execute_lifecycle_reports_quota_exceeded_via_phrasing_fallback(monkeypatch, tmp_path):
    script = tmp_path / "codex"
    script.write_text(
        "#!/bin/sh\n"
        "printf '%s' \"You've hit your usage limit. Try again in 3h 42m\"\n"
        "exit 1\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    main.store = main.ExecutionStore(str(tmp_path))

    with TestClient(main.app) as client:
        response = client.post("/execute", json={"repo": "owner/repo", "issue_number": 3, "prompt": "do it"})
        execution_id = response.json()["execution_id"]
        for _ in range(100):
            if client.get(f"/status/{execution_id}").json()["status"] != "running":
                break
            asyncio.run(asyncio.sleep(0.01))

        result = client.get(f"/result/{execution_id}").json()
        assert result["status"] == "quota_exceeded"
        assert result["resets_at"] == "3h 42m"
        assert result["quota_auto_resume"] is False


def test_resume_failure_logs_explicit_fallback_and_fresh_dispatch_succeeds(monkeypatch, tmp_path):
    script = tmp_path / "codex"
    script.write_text(
        "#!/bin/sh\n"
        "if [ \"$2\" = \"resume\" ]; then\n"
        "  printf '%s\\n' 'Session not found'\n"
        "  exit 1\n"
        "fi\n"
        "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"fresh-session\"}'\n"
        "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"fresh result\"}}'\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    main.store = main.ExecutionStore(str(tmp_path))

    with TestClient(main.app) as client:
        response = client.post(
            "/resume",
            json={"repo": "owner/repo", "issue_number": 3, "prompt": "do it", "session_id": "missing"},
        )
        execution_id = response.json()["execution_id"]
        for _ in range(100):
            if client.get(f"/status/{execution_id}").json()["status"] != "running":
                break
            asyncio.run(asyncio.sleep(0.01))

        result = client.get(f"/result/{execution_id}").json()
        assert result["status"] == "completed"
        assert result["result"] == "fresh result"
        assert result["session_id"] == "fresh-session"
        assert "[RESUME FAILED: falling back to a fresh Codex dispatch]" in result["log"]


def test_execute_lifecycle_genuine_failure_not_misclassified_as_quota(monkeypatch, tmp_path):
    script = tmp_path / "codex"
    script.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' 'unrelated tool error, nothing about quota here'\n"
        "exit 1\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    main.store = main.ExecutionStore(str(tmp_path))

    with TestClient(main.app) as client:
        response = client.post("/execute", json={"repo": "owner/repo", "issue_number": 3, "prompt": "do it"})
        execution_id = response.json()["execution_id"]
        for _ in range(100):
            if client.get(f"/status/{execution_id}").json()["status"] != "running":
                break
            asyncio.run(asyncio.sleep(0.01))

        result = client.get(f"/result/{execution_id}").json()
        assert result["status"] == "failed"
        assert result["resets_at"] is None
