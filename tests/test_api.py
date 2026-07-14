import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from task_runner import main
from task_runner.service import RunnerQueue
from tests.test_service import FakeRunner, PerRunnerStatusRunner, make_service


def create_task(service, task_id, issue_number, created_at, status="completed", **values):
    service.database.create_task(task_id, "owner/repo", issue_number, "codex", "http://runner")
    service.database.update(task_id, created_at=created_at, status=status, **values)


def test_tasks_endpoint_filters_paginates_and_returns_public_fields(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeRunner())
    now = datetime.now(timezone.utc)
    create_task(
        service,
        "newest",
        23,
        now.isoformat(),
        status="completed",
        completed_at=now.isoformat(),
        branch="main",
        pr_url="https://github.com/owner/repo/pull/1",
        session_id="session-1",
    )
    create_task(service, "running", 22, (now - timedelta(hours=1)).isoformat(), status="queued")
    create_task(service, "old", 21, (now - timedelta(days=2)).isoformat(), status="failed")
    monkeypatch.setattr(main, "service", service)
    client = TestClient(main.app)

    response = client.get("/api/tasks", params={"window": "7d", "limit": 1, "offset": 1})

    assert response.status_code == 200
    assert response.json() == {
        "tasks": [
            {
                "id": "running",
                "repo": "owner/repo",
                "issue_number": 22,
                "runner": "codex",
                "status": "running",
                "created_at": (now - timedelta(hours=1)).isoformat(),
                "completed_at": None,
                "branch": None,
                "pr_url": None,
                "resets_at": None,
                "session_id": None,
            }
        ],
        "running_count": 1,
    }
    assert [task["id"] for task in client.get("/api/tasks").json()["tasks"]] == ["newest", "running"]
    assert [task["id"] for task in client.get("/api/tasks?window=all").json()["tasks"]] == [
        "newest", "running", "old"
    ]
    assert client.get("/api/tasks?window=1h").status_code == 422
    assert client.get("/api/tasks?limit=0").status_code == 422


def test_health_and_mcp_routes_remain_available(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "service", make_service(tmp_path, FakeRunner()))
    client = TestClient(main.app)

    assert client.get("/").json() == {"service": "pacific-shift-task-runner", "status": "ok"}
    assert any(getattr(route, "path", None) == "/mcp" for route in main.app.routes)


def test_repo_registry_endpoint_returns_deploy_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "service", make_service(tmp_path, FakeRunner()))
    response = TestClient(main.app).get("/api/repos")
    assert response.status_code == 200
    assert response.json()["repos"][0]["repo"] == "owner/repo"
    assert response.json()["repos"][0]["dev"]["container"] == "app-dev"


async def wait_for_halt(service):
    for _ in range(100):
        queue = service._runner_queues["codex"]
        if queue.halt_state == "halted":
            return
        await asyncio.sleep(0.001)
    raise AssertionError("runner queue did not halt")


def test_queues_endpoint_reports_real_pending_position_and_halt_without_side_effects(
    tmp_path, monkeypatch
):
    async def arrange():
        service = make_service(
            tmp_path,
            PerRunnerStatusRunner({"http://runner": "failed"}),
            runners={"codex": "http://runner", "idle": "http://idle"},
        )
        first = await service.run_task("owner/repo", 1, "codex")
        second = await service.run_task("owner/repo", 2, "codex")
        third = await service.run_task("owner/repo", 3, "codex")
        await asyncio.gather(*service._jobs)
        await wait_for_halt(service)
        return service, first, second, third

    service, first, second, third = asyncio.run(arrange())
    monkeypatch.setattr(main, "service", service)
    before_tasks = service.database.list()
    client = TestClient(main.app)

    first_response = client.get("/api/queues")
    second_response = client.get("/api/queues")

    assert first_response.status_code == 200
    assert first_response.json() == second_response.json() == {
        "codex": {
            "active_task_id": None,
            "pending": [second["task_id"], third["task_id"]],
            "halt_state": "halted",
            "resumes_at": None,
        },
        "idle": {
            "active_task_id": None,
            "pending": [],
            "halt_state": None,
            "resumes_at": None,
        },
    }
    assert service.get_task_result(first["task_id"])["status"] == "failed"
    assert service.database.list() == before_tasks


def test_queues_endpoint_reports_quota_halt_state(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeRunner())
    service._runner_queues["codex"] = RunnerQueue(
        pending=deque(["quota-task", "next-task"]),
        halt_state="quota_halted",
        resumes_at="2026-07-13T01:00:00+00:00",
    )
    monkeypatch.setattr(main, "service", service)

    assert TestClient(main.app).get("/api/queues").json()["codex"] == {
        "active_task_id": None,
        "pending": ["quota-task", "next-task"],
        "halt_state": "quota_halted",
        "resumes_at": "2026-07-13T01:00:00+00:00",
    }
