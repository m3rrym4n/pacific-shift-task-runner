import asyncio

import pytest

from task_runner.config import ScheduledTask, Settings, parse_interval_seconds
from task_runner.database import Database
from task_runner.service import TaskService


class FakeGitHub:
    async def get_context(self, repo, issue_number):
        return "instructions", "Test issue", "acceptance criteria"


class FakeRunner:
    def __init__(self, statuses=None, result=None):
        self.statuses = iter(statuses or [{"status": "completed"}])
        self.result_data = result or {"result": "done", "log": "log output"}
        self.cancelled = False
        self.prompt = None

    async def execute(self, url, repo, issue_number, prompt):
        self.prompt = prompt
        return "execution-1"

    async def status(self, url, execution_id):
        return next(self.statuses)

    async def result(self, url, execution_id):
        return self.result_data

    async def cancel(self, url, execution_id):
        self.cancelled = True
        return True


def make_service(tmp_path, runner, **overrides):
    values = dict(database_path=str(tmp_path / "tasks.db"), runners={"codex": "http://runner"}, poll_interval_seconds=0)
    values.update(overrides)
    settings = Settings(**values)
    database = Database(settings.database_path)
    database.initialize()
    return TaskService(settings, database, FakeGitHub(), runner)


def test_interval_parser_accepts_seconds_minutes_hours_and_days():
    assert parse_interval_seconds("15") == 15
    assert parse_interval_seconds("2m") == 120
    assert parse_interval_seconds("1h") == 3600
    assert parse_interval_seconds("1d") == 86400


def test_interval_parser_rejects_non_positive_values():
    with pytest.raises(ValueError, match="greater than zero"):
        parse_interval_seconds("0s")


@pytest.mark.asyncio
async def test_real_dispatch_lifecycle_and_tools(tmp_path):
    runner = FakeRunner(result={"result": "structured report", "log": "abc"})
    service = make_service(tmp_path, runner)
    task_id = await service.run_task("owner/repo", 2, "codex")
    await asyncio.gather(*service._jobs)

    assert "instructions" in runner.prompt and "Test issue" in runner.prompt
    assert service.get_task_result(task_id)["result"] == "structured report"
    assert service.get_task_log(task_id)["log"] == "abc"
    assert service.list_tasks()[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_timeout_is_persisted_and_cancellation_attempted(tmp_path):
    runner = FakeRunner(statuses=[{"status": "running"}] * 100)
    service = make_service(tmp_path, runner, timeout_seconds=0.01, poll_interval_seconds=0.005)
    task_id = await service.run_task("owner/repo", 2, "codex")
    await asyncio.gather(*service._jobs)

    result = service.get_task_result(task_id)
    assert result["status"] == "timeout"
    assert runner.cancelled is True


@pytest.mark.asyncio
async def test_output_cap_is_visible(tmp_path):
    runner = FakeRunner(result={"result": "done", "log": "x" * 100})
    service = make_service(tmp_path, runner, output_cap_bytes=60)
    task_id = await service.run_task("owner/repo", 2, "codex")
    await asyncio.gather(*service._jobs)

    log = service.get_task_log(task_id)
    assert log["output_truncated"] is True
    assert "OUTPUT TRUNCATED" in log["log"]
    assert len(log["log"].encode()) <= 60


@pytest.mark.asyncio
async def test_unknown_runner_is_rejected_without_persistence(tmp_path):
    service = make_service(tmp_path, FakeRunner())
    with pytest.raises(ValueError, match="Unknown runner"):
        await service.run_task("owner/repo", 2, "claude")
    assert service.list_tasks() == []


@pytest.mark.asyncio
async def test_scheduler_fires_configured_task_without_manual_dispatch(tmp_path):
    runner = FakeRunner()
    scheduled_task = ScheduledTask(
        name="health-check",
        repo="owner/repo",
        issue_number=2,
        runner="codex",
        interval_seconds=0.01,
    )
    service = make_service(tmp_path, runner, scheduled_tasks=[scheduled_task])

    service.start_scheduler()
    for _ in range(20):
        if service.list_tasks():
            break
        await asyncio.sleep(0.01)
    await service.stop_scheduler()
    await asyncio.gather(*service._jobs)

    tasks = service.list_tasks()
    assert len(tasks) >= 1
    assert tasks[0]["repo"] == "owner/repo"
    assert tasks[0]["issue_number"] == 2
    assert tasks[0]["runner"] == "codex"
    assert tasks[0]["status"] == "completed"
