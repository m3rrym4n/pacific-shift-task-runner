import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from .config import OpsImageCheck, ScheduledTask, Settings
from .database import Database
from .dockhand import ContainerDeployResult, DockhandClient
from .github import GitHubClient
from .ops_images import codex_runner_tag
from .runner import RunnerClient


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunnerQueue:
    pending: deque[str] = field(default_factory=deque)
    active_task_id: str | None = None
    halt_state: Literal["halted", "quota_halted"] | None = None
    resumes_at: str | None = None


@dataclass(frozen=True)
class OpsImageRebuildJob:
    check: OpsImageCheck
    installed_version: str
    target_version: str


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
        self._internal_ops_jobs: dict[str, OpsImageRebuildJob] = {}
        self._queue_lock = asyncio.Lock()
        self._runner_queues = {name: RunnerQueue() for name in settings.runners}
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

    def resume_running_tasks(self) -> None:
        for task in self.database.list():
            if task["status"] != "running" or not task.get("execution_id"):
                continue
            if any(not job.done() and getattr(job, "_task_runner_task_id", None) == task["id"] for job in self._jobs):
                continue
            job = asyncio.create_task(
                self._resume_monitor(task["id"], task["runner_url"], task["execution_id"])
            )
            setattr(job, "_task_runner_task_id", task["id"])
            self._jobs.add(job)
            job.add_done_callback(self._jobs.discard)
            self.logger.info("Resumed monitoring task %s with execution %s", task["id"], task["execution_id"])

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
                receipt = await self.run_task(
                    scheduled_task.repo, scheduled_task.issue_number, scheduled_task.runner
                )
                self.logger.info(
                    "Scheduled task '%s' fired and created task %s",
                    scheduled_task.name,
                    receipt["task_id"],
                )
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

    async def run_task(self, repo: str, issue_number: int, runner_name: str) -> dict[str, Any]:
        if runner_name not in self.settings.runners:
            available = ", ".join(sorted(self.settings.runners)) or "none"
            raise ValueError(f"Unknown runner '{runner_name}'. Available runners: {available}")
        if "/" not in repo or issue_number < 1:
            raise ValueError("repo must be owner/name and issue_number must be positive")
        task_id = str(uuid.uuid4())
        self.database.create_task(task_id, repo, issue_number, runner_name, self.settings.runners[runner_name])
        async with self._queue_lock:
            queue = self._runner_queues.setdefault(runner_name, RunnerQueue())
            queue.pending.append(task_id)
            queue_length = len(queue.pending) + (1 if queue.active_task_id else 0)
            position = queue_length - 1
            status = "queued"
            if queue.halt_state is None and not queue.active_task_id and queue.pending[0] == task_id:
                status = "running"
            if queue.halt_state is None and not self._queue_worker_running(runner_name):
                job = asyncio.create_task(self._process_runner_queue(runner_name))
                setattr(job, "_task_runner_runner_name", runner_name)
                self._jobs.add(job)
                job.add_done_callback(self._jobs.discard)
            resumes_at = queue.resumes_at if queue.halt_state == "quota_halted" else None
        receipt = {
            "task_id": task_id,
            "status": status,
            "position": position,
            "queue_length": queue_length,
            "runner": runner_name,
        }
        if resumes_at is not None:
            receipt["resumes_at"] = resumes_at
        return receipt

    def _queue_worker_running(self, runner_name: str) -> bool:
        return any(
            not job.done() and getattr(job, "_task_runner_runner_name", None) == runner_name
            for job in self._jobs
        )

    async def clear_runner_halt(self, runner_name: str) -> dict[str, Any]:
        if runner_name not in self.settings.runners:
            available = ", ".join(sorted(self.settings.runners)) or "none"
            raise ValueError(f"Unknown runner '{runner_name}'. Available runners: {available}")
        async with self._queue_lock:
            queue = self._runner_queues.setdefault(runner_name, RunnerQueue())
            previous_halt_state = queue.halt_state
            if previous_halt_state is None:
                return {
                    "runner": runner_name,
                    "status": "not_halted",
                    "pending_count": len(queue.pending),
                }
            queue.halt_state = None
            queue.resumes_at = None
            if not queue.active_task_id and queue.pending and not self._queue_worker_running(runner_name):
                job = asyncio.create_task(self._process_runner_queue(runner_name))
                setattr(job, "_task_runner_runner_name", runner_name)
                self._jobs.add(job)
                job.add_done_callback(self._jobs.discard)
            pending_count = len(queue.pending)
        self.logger.info("Runner queue '%s' halt cleared manually", runner_name)
        return {
            "runner": runner_name,
            "status": "resumed",
            "previous_halt_state": previous_halt_state,
            "pending_count": pending_count,
        }

    async def cancel_queued_task(self, task_id: str) -> dict[str, Any]:
        task = self._required(task_id)
        runner_name = task["runner"]
        async with self._queue_lock:
            queue = self._runner_queues.setdefault(runner_name, RunnerQueue())
            if queue.active_task_id == task_id:
                raise ValueError(
                    f"Task '{task_id}' is active and cannot be cancelled as a queued task"
                )
            try:
                queue.pending.remove(task_id)
            except ValueError:
                raise ValueError(f"Task '{task_id}' is not pending in a runner queue") from None
            pending_count = len(queue.pending)
            self._internal_ops_jobs.pop(task_id, None)
            self.database.update(
                task_id,
                status="cancelled",
                error="Cancelled while queued before runner execution.",
                completed_at=utcnow(),
            )
        self.logger.info("Cancelled queued task %s on runner '%s'", task_id, runner_name)
        return {
            "task_id": task_id,
            "runner": runner_name,
            "status": "cancelled",
            "pending_count": pending_count,
        }

    async def _process_runner_queue(self, runner_name: str) -> None:
        while True:
            async with self._queue_lock:
                queue = self._runner_queues.setdefault(runner_name, RunnerQueue())
                if queue.halt_state is not None or queue.active_task_id or not queue.pending:
                    return
                task_id = queue.pending.popleft()
                queue.active_task_id = task_id
            try:
                await self._dispatch_or_run_internal(task_id)
                task = self.database.get(task_id) or {}
                if task.get("status") != "completed":
                    resets_at = task.get("resets_at")
                    resume_time = self._parse_resume_time(resets_at) if resets_at else None
                    if (
                        task.get("status") == "quota_exceeded"
                        and task.get("quota_auto_resume")
                        and task.get("session_id")
                        and resume_time is not None
                    ):
                        self.logger.warning(
                            "Runner queue '%s' quota-halted after task %s until %s",
                            runner_name,
                            task_id,
                            resets_at,
                        )
                        async with self._queue_lock:
                            queue = self._runner_queues.setdefault(runner_name, RunnerQueue())
                            queue.pending.appendleft(task_id)
                            queue.halt_state = "quota_halted"
                            queue.resumes_at = str(resets_at)
                        resume_job = asyncio.create_task(
                            self._resume_quota_halted_queue(runner_name, resume_time)
                        )
                        self._jobs.add(resume_job)
                        resume_job.add_done_callback(self._jobs.discard)
                        return
                    self.logger.error(
                        "Runner queue '%s' halted after task %s ended with status '%s': %s",
                        runner_name,
                        task_id,
                        task.get("status"),
                        task.get("error") or "no error detail recorded",
                    )
                    async with self._queue_lock:
                        queue = self._runner_queues.setdefault(runner_name, RunnerQueue())
                        queue.halt_state = "halted"
                    return
            except Exception:
                self.logger.exception("Runner queue '%s' halted while processing task %s", runner_name, task_id)
                async with self._queue_lock:
                    queue = self._runner_queues.setdefault(runner_name, RunnerQueue())
                    queue.halt_state = "halted"
                return
            finally:
                async with self._queue_lock:
                    queue = self._runner_queues.setdefault(runner_name, RunnerQueue())
                    if queue.active_task_id == task_id:
                        queue.active_task_id = None

    @staticmethod
    def _parse_resume_time(value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed

    async def _resume_quota_halted_queue(self, runner_name: str, resume_time: datetime) -> None:
        delay = max(0.0, (resume_time.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
        await asyncio.sleep(delay)
        while True:
            async with self._queue_lock:
                queue = self._runner_queues.setdefault(runner_name, RunnerQueue())
                if queue.halt_state != "quota_halted":
                    return
                if queue.active_task_id is None:
                    queue.halt_state = None
                    queue.resumes_at = None
                    break
            await asyncio.sleep(0)
        self.logger.info("Runner queue '%s' resumed after quota reset", runner_name)
        await self._process_runner_queue(runner_name)

    async def _dispatch_or_run_internal(self, task_id: str) -> None:
        ops_job = self._internal_ops_jobs.get(task_id)
        if ops_job is not None:
            await self._run_ops_image_rebuild(task_id, ops_job)
            return
        await self._dispatch(task_id)

    async def _dispatch(self, task_id: str) -> None:
        task = self.database.get(task_id)
        assert task is not None
        try:
            if task["status"] == "quota_exceeded" and task.get("session_id"):
                prompt = task.get("prompt") or ""
                self.logger.info(
                    "Resuming quota-interrupted task %s with Codex session %s",
                    task_id,
                    task["session_id"],
                )
                self.database.update(task_id, status="dispatching", completed_at=None)
                execution_id = await self.runner.resume(
                    task["runner_url"], task["repo"], task["issue_number"], prompt, task["session_id"]
                )
            else:
                agents, title, body = await self.github.get_context(task["repo"], task["issue_number"])
                prompt = self.build_prompt(task["repo"], task["issue_number"], agents, title, body)
                self.database.update(task_id, status="dispatching", prompt=prompt, started_at=utcnow())
                execution_id = await self.runner.execute(
                    task["runner_url"], task["repo"], task["issue_number"], prompt
                )
            self.database.update(task_id, status="running", execution_id=execution_id)
            await self._monitor_with_timeout(task_id, task["runner_url"], execution_id, self.settings.timeout_seconds)
        except asyncio.TimeoutError:
            await self._record_timeout(task_id, task["runner_url"])
        except Exception as exc:
            self.database.update(task_id, status="failed", error=f"{type(exc).__name__}: {exc}", completed_at=utcnow())

    async def _run_ops_image_rebuild(self, task_id: str, job: OpsImageRebuildJob) -> None:
        check = job.check
        prompt = self.build_ops_rebuild_prompt(check, job.installed_version, job.target_version)
        log_parts: list[str] = []
        self.database.update(task_id, status="running", prompt=prompt, started_at=utcnow())
        try:
            if self.dockhand is None:
                raise RuntimeError("Dockhand deploy capability is not configured")
            repo_sha = check.source_sha[:7]
            tag = codex_runner_tag(job.target_version, repo_sha)
            registry_host = _registry_host(check.registry)
            image = f"{registry_host}/{check.repository}:{tag}"
            log_parts.append(
                "\n".join(
                    [
                        "Ops Images codex-runner rebuild started.",
                        f"Trace issue: {check.repo}#{check.issue_number}",
                        f"Runner: {check.runner}",
                        f"Installed Codex version: {job.installed_version}",
                        f"Target Codex version: {job.target_version}",
                        f"Image: {image}",
                    ]
                )
            )
            if not await self.dockhand.container_uses_volume(check.start_container, check.auth_volume):
                raise RuntimeError(
                    f"Container '{check.start_container}' is not configured with required volume '{check.auth_volume}'"
                )
            log_parts.append(f"Verified required auth volume before deploy: {check.auth_volume}")
            build_command = [
                "buildctl",
                "--addr",
                check.buildkit_addr,
                "build",
                "--frontend",
                "dockerfile.v0",
                "--local",
                "context=/app/codex_runner",
                "--local",
                "dockerfile=/app/codex_runner",
                "--opt",
                f"build-arg:CODEX_VERSION={job.target_version}",
                "--output",
                f"type=image,name={image},push=true",
            ]
            log_parts.append(await self._run_command(build_command))
            prune_command = [
                "python",
                "/app/scripts/prune_zot_image_tags.py",
                "--registry",
                _registry_url(check.registry),
                "--repository",
                check.repository,
                "--keep",
                str(check.keep_tags),
            ]
            if check.insecure_tls:
                prune_command.append("--insecure-tls")
            log_parts.append(await self._run_command(prune_command))
            deploy_result = await self.dockhand.deploy_container_swap(check.stop_container, check.start_container)
            log_parts.append(
                "Deploy verified: "
                f"stopped={deploy_result.stopped_container}, started={deploy_result.started_container}, "
                f"status={deploy_result.status}, health={deploy_result.health_status}"
            )
            if not await self.dockhand.container_uses_volume(check.start_container, check.auth_volume):
                raise RuntimeError(
                    f"Required volume '{check.auth_volume}' is missing after deploy on '{check.start_container}'"
                )
            log_parts.append(f"Verified required auth volume after deploy: {check.auth_volume}")
            log = "\n\n".join(log_parts)
            capped_log, truncated = self._cap(log)
            self.database.update(
                task_id,
                status="completed",
                result=f"Built, pushed, pruned, and deployed {image}",
                log=capped_log,
                output_truncated=int(truncated),
                completed_at=utcnow(),
            )
        except Exception as exc:
            log = "\n\n".join(log_parts)
            capped_log, truncated = self._cap(log)
            self.database.update(
                task_id,
                status="failed",
                log=capped_log,
                output_truncated=int(truncated),
                error=f"{type(exc).__name__}: {exc}",
                completed_at=utcnow(),
            )
        finally:
            self._internal_ops_jobs.pop(task_id, None)

    async def _run_command(self, command: list[str]) -> str:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        output = stdout.decode("utf-8", errors="replace")
        command_text = " ".join(command)
        if process.returncode != 0:
            raise RuntimeError(f"Command failed ({process.returncode}): {command_text}\n{output}")
        return f"$ {command_text}\n{output}".strip()

    async def _resume_monitor(self, task_id: str, url: str, execution_id: str) -> None:
        try:
            if await self._record_terminal_if_available(task_id, url, execution_id):
                return
            remaining_timeout = self._remaining_timeout(task_id)
            if remaining_timeout <= 0:
                await self._record_timeout(task_id, url)
                return
            await self._monitor_with_timeout(task_id, url, execution_id, remaining_timeout)
        except asyncio.TimeoutError:
            await self._record_timeout(task_id, url)
        except Exception as exc:
            self.database.update(task_id, status="failed", error=f"{type(exc).__name__}: {exc}", completed_at=utcnow())

    async def _monitor_with_timeout(self, task_id: str, url: str, execution_id: str, timeout: float) -> None:
        await asyncio.wait_for(self._monitor(task_id, url, execution_id), timeout=timeout)

    async def _monitor(self, task_id: str, url: str, execution_id: str) -> None:
        while True:
            if await self._record_terminal_if_available(task_id, url, execution_id):
                return
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _record_terminal_if_available(self, task_id: str, url: str, execution_id: str) -> bool:
        status_data = await self.runner.status(url, execution_id)
        status = status_data.get("status")
        if status not in {"completed", "failed", "timeout", "quota_exceeded"}:
            return False
        result_data = await self.runner.result(url, execution_id)
        result = result_data.get("result") or result_data.get("report") or ""
        log = result_data.get("log") or result_data.get("stdout") or ""
        capped_log, truncated = self._cap(str(log))
        self.database.update(
            task_id, status=status, result=str(result), log=capped_log,
            output_truncated=int(truncated), error=result_data.get("error"),
            resets_at=result_data.get("resets_at"), completed_at=utcnow(),
            session_id=result_data.get("session_id"),
            branch=result_data.get("branch"), pr_url=result_data.get("pr_url"),
            quota_auto_resume=int(bool(result_data.get("quota_auto_resume"))),
        )
        return True

    async def _record_timeout(self, task_id: str, url: str) -> None:
        current = self.database.get(task_id) or {}
        execution_id = current.get("execution_id")
        cancelled = bool(execution_id) and await self.runner.cancel(url, execution_id)
        detail = "Runner cancellation accepted." if cancelled else "Runner cancellation unavailable or rejected."
        self.database.update(task_id, status="timeout", error=f"Task exceeded {self.settings.timeout_seconds:g}s. {detail}", completed_at=utcnow())

    def _remaining_timeout(self, task_id: str) -> float:
        task = self.database.get(task_id) or {}
        started_at = task.get("started_at")
        if not started_at:
            return self.settings.timeout_seconds
        started = datetime.fromisoformat(str(started_at))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return max(0, self.settings.timeout_seconds - elapsed)

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

    @staticmethod
    def build_ops_rebuild_prompt(check: OpsImageCheck, installed: str, target: str) -> str:
        return f"""# Internal Ops Images rebuild

Trace issue: {check.repo}#{check.issue_number}
Runner queue: {check.runner}
Installed Codex version: {installed}
Target Codex version: {target}

This is an internal maintenance job created by Task Runner after version drift
was detected. It is tied to the configured trace issue so every rebuild cycle
has a durable written reference in the normal task log/result model.
"""

    def get_task_result(self, task_id: str) -> dict[str, Any]:
        task = self._required(task_id)
        return {
            key: task[key]
            for key in (
                "id", "status", "result", "error", "resets_at", "session_id", "output_truncated",
                "created_at", "completed_at",
            )
        }

    def get_task_log(self, task_id: str) -> dict[str, Any]:
        task = self._required(task_id)
        return {"id": task_id, "status": task["status"], "log": task["log"], "output_truncated": bool(task["output_truncated"])}

    def list_tasks(self) -> list[dict[str, Any]]:
        keys = ("id", "repo", "issue_number", "runner", "status", "output_truncated", "created_at", "completed_at")
        return [{key: task[key] for key in keys} for task in self.database.list()]

    def list_dashboard_tasks(
        self, window: Literal["24h", "7d", "30d", "all"], limit: int | None, offset: int
    ) -> dict[str, Any]:
        cutoffs = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}
        cutoff = None
        if window != "all":
            cutoff = datetime.now(timezone.utc).timestamp() - cutoffs[window] * 60 * 60

        matching = []
        for task in self.database.list():
            created_at = datetime.fromisoformat(str(task["created_at"]).replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if cutoff is None or created_at.timestamp() >= cutoff:
                matching.append(task)

        running_count = sum(task["status"] in {"queued", "dispatching", "running"} for task in matching)
        selected = matching[offset : offset + limit if limit is not None else None]
        keys = (
            "id", "repo", "issue_number", "runner", "status", "created_at", "completed_at",
            "branch", "pr_url", "resets_at", "session_id",
        )
        tasks = []
        for task in selected:
            item = {key: task[key] for key in keys}
            if item["status"] in {"queued", "dispatching"}:
                item["status"] = "running"
            tasks.append(item)
        return {"tasks": tasks, "running_count": running_count}

    async def get_queue_states(self) -> dict[str, dict[str, Any]]:
        async with self._queue_lock:
            return {
                runner_name: {
                    "active_task_id": queue.active_task_id,
                    "pending": list(queue.pending),
                    "halt_state": queue.halt_state,
                    "resumes_at": queue.resumes_at,
                }
                for runner_name, queue in self._runner_queues.items()
            }

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
        task_id = str(uuid.uuid4())
        self.database.create_task(
            task_id,
            ops_check.repo,
            ops_check.issue_number,
            ops_check.runner,
            self.settings.runners[ops_check.runner],
        )
        self._internal_ops_jobs[task_id] = OpsImageRebuildJob(ops_check, installed, latest)
        async with self._queue_lock:
            queue = self._runner_queues.setdefault(ops_check.runner, RunnerQueue())
            queue.pending.append(task_id)
            if queue.halt_state is None and not self._queue_worker_running(ops_check.runner):
                job = asyncio.create_task(self._process_runner_queue(ops_check.runner))
                setattr(job, "_task_runner_runner_name", ops_check.runner)
                self._jobs.add(job)
                job.add_done_callback(self._jobs.discard)
        return True

    def _required(self, task_id: str) -> dict[str, Any]:
        task = self.database.get(task_id)
        if task is None:
            raise ValueError(f"Unknown task_id: {task_id}")
        return task


def _registry_host(registry: str) -> str:
    return registry.removeprefix("https://").removeprefix("http://").rstrip("/")


def _registry_url(registry: str) -> str:
    if registry.startswith("http://") or registry.startswith("https://"):
        return registry.rstrip("/")
    return f"https://{registry.rstrip('/')}"
