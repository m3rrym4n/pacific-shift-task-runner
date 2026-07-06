from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from .config import Settings
from .database import Database
from .github import GitHubClient
from .runner import RunnerClient
from .service import TaskService


settings = Settings.from_env()
database = Database(settings.database_path)
service = TaskService(settings, database, GitHubClient(settings.github_token), RunnerClient())
mcp = FastMCP("Pacific Shift Task Runner", stateless_http=True, streamable_http_path="/")


@mcp.tool()
async def run_task(repo: str, issue_number: int, runner: str) -> str:
    """Dispatch one GitHub issue to a configured runner and return its task ID."""
    return await service.run_task(repo, issue_number, runner)


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
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Pacific Shift Task Runner", lifespan=lifespan)


@app.get("/")
def health() -> dict[str, str]:
    return {"service": "pacific-shift-task-runner", "status": "ok"}


app.mount("/mcp", mcp_app)
