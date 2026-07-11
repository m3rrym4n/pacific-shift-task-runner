import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import OpsImageCheck, ScheduledTask, Settings
from .database import Database
from .dockhand import ContainerDeployResult, DockhandClient
from .github import GitHubClient
from .runner import RunnerClient


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        github: GitHubClient,
        runner: RunnerClient,
        dockhand: DockhandClient | None = None,
    ):
        self.settings = settings
        self.database = database
        self.github = github
        self.runner = runner
        self.dockhand = dockhand
        self._jobs: set[asyncio.Task] = set()
        self._scheduler_jobs: set[asyncio.Task] = set()
        self.logger = logging.getLogger(__name__)

    def start_scheduler(self) -> None:
        if self._scheduler_jobs:
            return
        for scheduled_task in self.settings.scheduled_tasks:
            job = asyncio.create_task(self._scheduled_loop(scheduled_task))
            self._scheduler_jobs.add(job)
            job.add_done_callback(self._scheduler_jobs.discard)
            self.logger.info(
                "Scheduled task '%s' enabled for %s#%s on runner '%s' every %gs",
                scheduled_task.name,
                scheduled_task.repo,
                scheduled_task.issue_number,
                scheduled_task.runner,
                scheduled_task.interval_seconds,
            )
        for ops_check in self.settings.ops_image_checks:
            job = asyncio.create_task(self._ops_image_loop(ops_check))
            self._scheduler_jobs.add(job)
            job.add_done_callback(self._scheduler_jobs.discard)
            self.logger.info(
                "Ops image check '%s' enabled for runner '%s' every %gs",
                ops_check.name,
                ops_check.runner,
                ops_check.interval_seconds,
            )

    async def stop_scheduler(self) -> None:
        if not self._scheduler_jobs:
            return
        for job in self._scheduler_jobs:
            job.cancel()
        await asyncio.gather(*self._scheduler_jobs, return_exceptions=True)
        self._scheduler_jobs.clear()

    async def _scheduled_loop(self, scheduled_task: ScheduledTask) -> None:
        while True:
            await asyncio.sleep(scheduled_task.interval_seconds)
            try:
                task_id = await self.run_task(
                    scheduled_task.repo, scheduled_task.issue_number, scheduled_task.runner
                )
                self.logger.info("Scheduled task '%s' fired and created task %s", scheduled_task.name, task_id)
            except Exception:
                self.logger.exception("Scheduled task '%s' failed to fire", scheduled_task.name)

    async def _ops_image_loop(self, ops_check: OpsImageCheck) -> None:
        while True:
            await asyncio.sleep(ops_check.interval_seconds)
            try:
                dispatched = await self.check_ops_image(ops_check)
                if dispatched:
                    self.logger.info("Ops image check '%s' detected drift and dispatched rebuild", ops_check.name)
                else:
                    self.logger.info("Ops image check '%s' completed with no drift", ops_check.name)
            except Exception:
                self.logger.exception("Ops image check '%s' failed", ops_check.name)

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

    async def deploy_container_swap(self, stop_container: str, start_container: str) -> ContainerDeployResult:
        if self.dockhand is None:
            raise RuntimeError("Dockhand deploy capability is not configured")
        return await self.dockhand.deploy_container_swap(stop_container, start_container)

    async def check_ops_image(self, ops_check: OpsImageCheck) -> bool:
        if ops_check.runner not in self.settings.runners:
            available = ", ".join(sorted(self.settings.runners)) or "none"
            raise ValueError(f"Unknown runner '{ops_check.runner}'. Available runners: {available}")
        version_data = await self.runner.codex_version(self.settings.runners[ops_check.runner])
        installed = str(version_data.get("installed") or "")
        latest = str(version_data.get("latest") or "")
        if not installed or not latest:
            raise ValueError("Codex version drift response must include installed and latest")
        drift_value = version_data.get("drift_detected")
        drift_detected = drift_value if isinstance(drift_value, bool) else installed != latest
        if not drift_detected:
            return False
        await self.github.dispatch_workflow(
            ops_check.workflow_repo,
            ops_check.workflow_id,
            ops_check.ref,
            {
                "installed_codex_version": installed,
                "target_codex_version": latest,
                "runner": ops_check.runner,
            },
        )
        return True

    def _required(self, task_id: str) -> dict[str, Any]:
        task = self.database.get(task_id)
        if task is None:
            raise ValueError(f"Unknown task_id: {task_id}")
        return task
