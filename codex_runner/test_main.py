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
