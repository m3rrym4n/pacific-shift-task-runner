import asyncio
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, field_validator


ExecutionStatus = Literal["running", "completed", "failed", "timeout"]


class ExecuteRequest(BaseModel):
    repo: str
    prompt: str
    issue_number: int | None = None

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, value: str) -> str:
        if value.count("/") != 1 or any(not part.strip() for part in value.split("/")):
            raise ValueError("repo must be owner/name")
        return value


@dataclass
class Execution:
    id: str
    workspace: Path
    status: ExecutionStatus = "running"
    process: asyncio.subprocess.Process | None = None
    result: str = ""
    log: str = ""
    error: str | None = None
    exit_code: int | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class ExecutionStore:
    def __init__(self, workspace_root: str | None = None) -> None:
        self.workspace_root = workspace_root
        self.executions: dict[str, Execution] = {}

    def create(self) -> Execution:
        execution_id = str(uuid.uuid4())
        workspace = Path(tempfile.mkdtemp(prefix=f"codex-{execution_id}-", dir=self.workspace_root))
        execution = Execution(id=execution_id, workspace=workspace)
        self.executions[execution_id] = execution
        return execution

    def get(self, execution_id: str) -> Execution:
        execution = self.executions.get(execution_id)
        if execution is None:
            raise HTTPException(status_code=404, detail="Unknown execution_id")
        return execution


store = ExecutionStore(os.getenv("CODEX_RUNNER_WORKSPACE_ROOT"))
app = FastAPI(title="Pacific Shift Codex Runner")


def _final_message(output: str) -> str:
    final = ""
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                final = item["text"]
    return final


def _runner_prompt(request: ExecuteRequest) -> str:
    return f"""# Runner workspace

This is a fresh, intentionally empty workspace for {request.repo}. Use shell
commands to clone that repository into the current directory, then perform all
required git and GitHub operations yourself. `GITHUB_TOKEN` is available and
the `gh` CLI is installed and authenticated. Do not use GitHub MCP mutation
tools; use `git` and `gh` from the shell so the non-interactive run does not
pause for connector approval.

{request.prompt}
"""


async def _run(execution: Execution, request: ExecuteRequest) -> None:
    command = [
        "codex",
        "exec",
        _runner_prompt(request),
        "--json",
        "--sandbox",
        "workspace-write",
        "--full-auto",
        "--skip-git-repo-check",
    ]
    try:
        execution.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=execution.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        stdout, _ = await execution.process.communicate()
        execution.exit_code = execution.process.returncode
        execution.log = stdout.decode("utf-8", errors="replace")
        execution.result = _final_message(execution.log)
        if execution.exit_code == 0:
            execution.status = "completed"
        else:
            execution.status = "failed"
            execution.error = f"codex exec exited with code {execution.exit_code}"
    except asyncio.CancelledError:
        execution.status = "timeout"
        execution.error = "Execution cancelled by orchestrator"
        if execution.process and execution.process.returncode is None:
            execution.process.terminate()
            try:
                await asyncio.wait_for(execution.process.wait(), timeout=10)
            except asyncio.TimeoutError:
                execution.process.kill()
                await execution.process.wait()
        raise
    except Exception as exc:
        execution.status = "failed"
        execution.error = f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(execution.workspace, ignore_errors=True)


@app.get("/")
def health() -> dict[str, str]:
    return {"service": "pacific-shift-codex-runner", "status": "ok"}


@app.post("/execute", status_code=status.HTTP_202_ACCEPTED)
async def execute(request: ExecuteRequest) -> dict[str, str]:
    execution = store.create()
    execution.task = asyncio.create_task(_run(execution, request))
    return {"execution_id": execution.id}


@app.get("/status/{execution_id}")
def execution_status(execution_id: str) -> dict[str, str]:
    return {"status": store.get(execution_id).status}


@app.get("/result/{execution_id}")
def execution_result(execution_id: str) -> dict[str, str | int | None]:
    execution = store.get(execution_id)
    return {
        "status": execution.status,
        "result": execution.result,
        "log": execution.log,
        "error": execution.error,
        "exit_code": execution.exit_code,
    }


@app.delete("/execute/{execution_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_execution(execution_id: str) -> Response:
    execution = store.get(execution_id)
    if execution.task and not execution.task.done():
        execution.task.cancel()
        try:
            await execution.task
        except asyncio.CancelledError:
            pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)
