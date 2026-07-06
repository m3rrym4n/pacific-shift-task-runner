import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .database import Database
from .github import GitHubClient
from .runner import RunnerClient


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskService:
    def __init__(self, settings: Settings, database: Database, github: GitHubClient, runner: RunnerClient):
        self.settings = settings
        self.database = database
        self.github = github
        self.runner = runner
        self._jobs: set[asyncio.Task] = set()

    async def run_task(self, repo: str, issue_number: int, runner_name: str) -> str:
        if runner_name not in self.settings.runners:
            available = ", ".join(sorted(self.settings.runners)) or "none"
            raise ValueError(f"Unknown runner '{runner_name}'. Available runners: {available}")
        if "/" not in repo or issue_number < 1:
            raise ValueError("repo must be owner/name and issue_number must be positive")
        task_id = str(uuid.uuid4())
        self.database.create_task(task_id, repo, issue_number, runner_name, self.settings.runners[runner_name])
        job = asyncio.create_task(self._dispatch(task_id))
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)
        return task_id

    async def _dispatch(self, task_id: str) -> None:
        task = self.database.get(task_id)
        assert task is not None
        try:
            agents, title, body = await self.github.get_context(task["repo"], task["issue_number"])
            prompt = self.build_prompt(task["repo"], task["issue_number"], agents, title, body)
            self.database.update(task_id, status="dispatching", prompt=prompt, started_at=utcnow())
            execution_id = await self.runner.execute(
                task["runner_url"], task["repo"], task["issue_number"], prompt
            )
            self.database.update(task_id, status="running", execution_id=execution_id)
            await asyncio.wait_for(
                self._monitor(task_id, task["runner_url"], execution_id),
                timeout=self.settings.timeout_seconds,
            )
        except asyncio.TimeoutError:
            current = self.database.get(task_id) or {}
            execution_id = current.get("execution_id")
            cancelled = bool(execution_id) and await self.runner.cancel(task["runner_url"], execution_id)
            detail = "Runner cancellation accepted." if cancelled else "Runner cancellation unavailable or rejected."
            self.database.update(task_id, status="timeout", error=f"Task exceeded {self.settings.timeout_seconds:g}s. {detail}", completed_at=utcnow())
        except Exception as exc:
            self.database.update(task_id, status="failed", error=f"{type(exc).__name__}: {exc}", completed_at=utcnow())

    async def _monitor(self, task_id: str, url: str, execution_id: str) -> None:
        while True:
            status_data = await self.runner.status(url, execution_id)
            status = status_data.get("status")
            if status in {"completed", "failed", "timeout"}:
                result_data = await self.runner.result(url, execution_id)
                result = result_data.get("result") or result_data.get("report") or ""
                log = result_data.get("log") or result_data.get("stdout") or ""
                capped_log, truncated = self._cap(str(log))
                self.database.update(
                    task_id, status=status, result=str(result), log=capped_log,
                    output_truncated=int(truncated), error=result_data.get("error"), completed_at=utcnow(),
                )
                return
            await asyncio.sleep(self.settings.poll_interval_seconds)

    def _cap(self, text: str) -> tuple[str, bool]:
        encoded = text.encode("utf-8")
        if len(encoded) <= self.settings.output_cap_bytes:
            return text, False
        marker = f"\n[OUTPUT TRUNCATED: exceeded {self.settings.output_cap_bytes} bytes]"
        budget = max(0, self.settings.output_cap_bytes - len(marker.encode()))
        shortened = encoded[:budget].decode("utf-8", errors="ignore")
        return shortened + marker, True

    @staticmethod
    def build_prompt(repo: str, issue_number: int, agents: str, title: str, body: str) -> str:
        return f"""# Task

Work on GitHub issue #{issue_number} in {repo}.

## Repository instructions (AGENTS.md)

{agents}

## GitHub issue #{issue_number}: {title}

{body}

Follow the repository instructions and issue acceptance criteria. Keep work within this issue's scope. Run the required tests and provide the required structured final report.
"""

    def get_task_result(self, task_id: str) -> dict[str, Any]:
        task = self._required(task_id)
        return {key: task[key] for key in ("id", "status", "result", "error", "output_truncated", "created_at", "completed_at")}

    def get_task_log(self, task_id: str) -> dict[str, Any]:
        task = self._required(task_id)
        return {"id": task_id, "status": task["status"], "log": task["log"], "output_truncated": bool(task["output_truncated"])}

    def list_tasks(self) -> list[dict[str, Any]]:
        keys = ("id", "repo", "issue_number", "runner", "status", "output_truncated", "created_at", "completed_at")
        return [{key: task[key] for key in keys} for task in self.database.list()]

    def _required(self, task_id: str) -> dict[str, Any]:
        task = self.database.get(task_id)
        if task is None:
            raise ValueError(f"Unknown task_id: {task_id}")
        return task
