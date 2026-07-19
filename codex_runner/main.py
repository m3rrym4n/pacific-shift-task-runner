import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, field_validator


ExecutionStatus = Literal["running", "completed", "failed", "timeout", "quota_exceeded"]


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


class ResumeRequest(ExecuteRequest):
    session_id: str


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
    resets_at: str | None = None
    session_id: str | None = None
    quota_auto_resume: bool = False
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


def _parse_version(output: str) -> str:
    for token in output.replace("\n", " ").split():
        parts = token.strip().split(".")
        if len(parts) >= 3 and all(part.isdigit() for part in parts[:3]):
            return ".".join(parts[:3])
    raise ValueError(f"Could not parse version from output: {output!r}")


def _command_output(command: list[str], timeout_seconds: float = 20) -> str:
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_seconds,
    )
    return completed.stdout.strip()


def get_installed_codex_version() -> str:
    return _parse_version(_command_output(["codex", "--version"]))


def get_latest_codex_version() -> str:
    return _parse_version(_command_output(["npm", "view", "@openai/codex", "version"]))


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


_QUOTA_PHRASES = ("you have reached your usage limit", "you've hit your usage limit")
_RESET_PHRASE_PATTERN = re.compile(r"try again in\s+([0-9]+h\s*[0-9]*m?|[0-9]+m)", re.IGNORECASE)


@dataclass
class QuotaExhaustion:
    resets_at: str | None
    structured: bool


def _detect_quota_exhaustion(output: str) -> QuotaExhaustion | None:
    """Detect Codex quota/rate-limit exhaustion as a distinct condition.

    Primary path: parse `token_count` JSONL events for a `rate_limits` payload
    with per-scope `used_percent`/`resets_at` fields. Fallback path: match
    known quota-exhaustion phrasing in the raw output, for cases where a
    structured event isn't present. Detection relies only on this task's own
    output/exit behavior, not on querying Codex's separate self-reported
    `/status` (which has a known "phantom limit" discrepancy upstream).
    """
    resets_at: str | None = None
    quota_detected = False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "token_count":
            continue
        rate_limits = event.get("rate_limits")
        if not isinstance(rate_limits, dict):
            continue
        for scope in ("primary", "secondary"):
            bucket = rate_limits.get(scope)
            if not isinstance(bucket, dict):
                continue
            used_percent = bucket.get("used_percent")
            if isinstance(used_percent, (int, float)) and used_percent >= 100:
                quota_detected = True
                candidate = bucket.get("resets_at") or rate_limits.get("resets_at")
                if isinstance(candidate, str):
                    resets_at = candidate

    if quota_detected:
        return QuotaExhaustion(resets_at=resets_at, structured=True)

    lowered = output.lower()
    if any(phrase in lowered for phrase in _QUOTA_PHRASES):
        match = _RESET_PHRASE_PATTERN.search(output)
        return QuotaExhaustion(resets_at=match.group(1) if match else None, structured=False)

    return None


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
        "--json",
        "--sandbox",
        "workspace-write",
        "--full-auto",
        "--skip-git-repo-check",
        "--config",
        "sandbox_workspace_write.network_access=true",
        "--config",
        "shell_environment_policy.inherit=all",
        _runner_prompt(request),
    ]
    await _run_command(execution, request, command)


async def _run_resume(execution: Execution, request: ResumeRequest) -> None:
    command = [
        "codex",
        "exec",
        "resume",
        "--json",
        "--skip-git-repo-check",
        request.session_id,
        "Continue the interrupted task from where you stopped. Complete the original request.",
    ]
    await _run_command(execution, request, command, fallback_to_fresh=True)


async def _run_command(
    execution: Execution,
    request: ExecuteRequest,
    command: list[str],
    fallback_to_fresh: bool = False,
) -> None:
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
        execution.session_id = _session_id(execution.log)
        if fallback_to_fresh and execution.exit_code != 0 and _detect_quota_exhaustion(execution.log) is None:
            resume_log = execution.log
            marker = "[RESUME FAILED: falling back to a fresh Codex dispatch]"
            await _run(execution, request)
            execution.log = f"{resume_log.rstrip()}\n{marker}\n{execution.log}"
            return
        execution.result = _final_message(execution.log)
        quota = _detect_quota_exhaustion(execution.log)
        if quota is not None:
            execution.status = "quota_exceeded"
            execution.resets_at = quota.resets_at
            execution.quota_auto_resume = quota.structured
            execution.error = "Codex usage limit reached." + (
                f" Resets at {quota.resets_at}." if quota.resets_at else ""
            )
        elif execution.exit_code == 0:
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


def _session_id(output: str) -> str | None:
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            return event["thread_id"]
    return None


@app.get("/")
def health() -> dict[str, str]:
    return {"service": "pacific-shift-codex-runner", "status": "ok"}


@app.get("/codex/version")
def codex_version() -> dict[str, str | bool]:
    installed = get_installed_codex_version()
    latest = get_latest_codex_version()
    return {
        "installed": installed,
        "latest": latest,
        "drift_detected": installed != latest,
    }


@app.post("/execute", status_code=status.HTTP_202_ACCEPTED)
async def execute(request: ExecuteRequest) -> dict[str, str]:
    execution = store.create()
    execution.task = asyncio.create_task(_run(execution, request))
    return {"execution_id": execution.id}


@app.post("/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume(request: ResumeRequest) -> dict[str, str]:
    execution = store.create()
    execution.task = asyncio.create_task(_run_resume(execution, request))
    return {"execution_id": execution.id}


@app.get("/status/{execution_id}")
def execution_status(execution_id: str) -> dict[str, str]:
    return {"status": store.get(execution_id).status}


@app.get("/result/{execution_id}")
def execution_result(execution_id: str) -> dict[str, object]:
    execution = store.get(execution_id)
    return {
        "status": execution.status,
        "result": execution.result,
        "log": execution.log,
        "error": execution.error,
        "exit_code": execution.exit_code,
        "resets_at": execution.resets_at,
        "session_id": execution.session_id,
        "quota_auto_resume": execution.quota_auto_resume,
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
