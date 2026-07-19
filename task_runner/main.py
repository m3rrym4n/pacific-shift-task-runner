from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, status
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from .config import Settings
from .database import Database
from .dockhand import DockhandClient
from .github import GitHubClient
from .runner import RunnerClient
from .service import TaskService


settings = Settings.from_env()
database = Database(settings.database_path)
service = TaskService(
    settings,
    database,
    GitHubClient(settings.github_token),
    RunnerClient(),
    DockhandClient(
        settings.dockhand_url,
        settings.dockhand_token,
        env=settings.dockhand_env,
        verify_timeout_seconds=settings.dockhand_verify_timeout_seconds,
        verify_interval_seconds=settings.dockhand_verify_interval_seconds,
    ),
)
mcp = FastMCP("Pacific Shift Task Runner", stateless_http=True, streamable_http_path="/")


@mcp.tool()
async def run_task(repo: str, issue_number: int, runner: str) -> dict:
    """Queue one GitHub issue for a configured runner and return queue placement."""
    return await service.run_task(repo, issue_number, runner)


@mcp.tool()
async def clear_runner_halt(runner: str) -> dict:
    """Clear one runner queue's halt and resume its remaining pending tasks."""
    return await service.clear_runner_halt(runner)


@mcp.tool()
async def cancel_queued_task(task_id: str) -> dict:
    """Cancel one task only if it has not started and is still queued."""
    return await service.cancel_queued_task(task_id)


@mcp.tool()
def get_task_result(task_id: str) -> dict:
    """Return the current state and final structured result for a task."""
    return service.get_task_result(task_id)


@mcp.tool()
def get_task_log(task_id: str) -> dict:
    """Return capped runner output and its truncation flag for a task."""
    return service.get_task_log(task_id)


@mcp.tool()
def list_tasks() -> list[dict]:
    """List dispatched tasks, newest first."""
    return service.list_tasks()


mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.initialize()
    service.resume_running_tasks()
    service.start_scheduler()
    async with mcp.session_manager.run():
        try:
            yield
        finally:
            await service.stop_scheduler()


app = FastAPI(title="Pacific Shift Task Runner", lifespan=lifespan)


class RunTaskRequest(BaseModel):
    repo: str
    issue_number: int
    runner: str


@app.get("/")
def health() -> dict[str, str]:
    return {"service": "pacific-shift-task-runner", "status": "ok"}


@app.get("/api/tasks")
def api_tasks(
    window: Literal["24h", "7d", "30d", "all"] = "24h",
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return service.list_dashboard_tasks(window, limit, offset)


@app.post("/api/tasks", status_code=status.HTTP_202_ACCEPTED)
async def api_run_task(request: RunTaskRequest) -> dict:
    try:
        return await service.run_task(request.repo, request.issue_number, request.runner)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.get("/api/tasks/{task_id}")
def api_task_result(task_id: str) -> dict:
    try:
        return service.get_task_result(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@app.get("/api/tasks/{task_id}/log")
def api_task_log(task_id: str) -> dict:
    try:
        return service.get_task_log(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str) -> dict:
    try:
        return await service.cancel_queued_task(task_id)
    except ValueError as exc:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if str(exc).startswith("Unknown task_id:")
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.get("/api/queues")
async def api_queues() -> dict:
    return await service.get_queue_states()


@app.post("/api/queues/{runner}/clear-halt")
async def api_clear_runner_halt(runner: str) -> dict:
    try:
        return await service.clear_runner_halt(runner)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@app.get("/api/repos")
def api_repos() -> dict:
    return {"repos": service.list_repo_configs()}


app.mount("/mcp", mcp_app)
