import json
import os
from dataclasses import dataclass
from typing import Any


def parse_interval_seconds(value: Any) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        interval = float(value)
    elif isinstance(value, str):
        text = value.strip().lower()
        if not text:
            raise ValueError("scheduled task interval must not be empty")
        unit = text[-1]
        multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit)
        if multiplier is None:
            interval = float(text)
        else:
            interval = float(text[:-1]) * multiplier
    else:
        raise ValueError("scheduled task interval must be a number or string")
    if interval <= 0:
        raise ValueError("scheduled task interval must be greater than zero")
    return interval


@dataclass(frozen=True)
class ScheduledTask:
    name: str
    repo: str
    issue_number: int
    runner: str
    interval_seconds: float

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ScheduledTask":
        name = value.get("name")
        repo = value.get("repo")
        issue_number = value.get("issue_number")
        runner = value.get("runner")
        interval = value.get("interval", value.get("interval_seconds"))
        if not isinstance(name, str) or not name:
            raise ValueError("scheduled task name must be a non-empty string")
        if not isinstance(repo, str) or "/" not in repo:
            raise ValueError("scheduled task repo must be owner/name")
        if not isinstance(issue_number, int) or issue_number < 1:
            raise ValueError("scheduled task issue_number must be a positive integer")
        if not isinstance(runner, str) or not runner:
            raise ValueError("scheduled task runner must be a non-empty string")
        if interval is None:
            raise ValueError("scheduled task interval is required")
        return cls(name, repo, issue_number, runner, parse_interval_seconds(interval))


@dataclass(frozen=True)
class OpsImageCheck:
    name: str
    runner: str
    workflow_repo: str
    workflow_id: str
    ref: str
    interval_seconds: float

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "OpsImageCheck":
        name = value.get("name")
        runner = value.get("runner")
        workflow_repo = value.get("workflow_repo")
        workflow_id = value.get("workflow_id")
        ref = value.get("ref", "main")
        interval = value.get("interval", value.get("interval_seconds"))
        if not isinstance(name, str) or not name:
            raise ValueError("ops image check name must be a non-empty string")
        if not isinstance(runner, str) or not runner:
            raise ValueError("ops image check runner must be a non-empty string")
        if not isinstance(workflow_repo, str) or "/" not in workflow_repo:
            raise ValueError("ops image check workflow_repo must be owner/name")
        if not isinstance(workflow_id, str) or not workflow_id:
            raise ValueError("ops image check workflow_id must be a non-empty string")
        if not isinstance(ref, str) or not ref:
            raise ValueError("ops image check ref must be a non-empty string")
        if interval is None:
            raise ValueError("ops image check interval is required")
        return cls(name, runner, workflow_repo, workflow_id, ref, parse_interval_seconds(interval))


@dataclass(frozen=True)
class Settings:
    database_path: str = "/data/tasks.db"
    runners: dict[str, str] = None  # type: ignore[assignment]
    scheduled_tasks: list[ScheduledTask] = None  # type: ignore[assignment]
    ops_image_checks: list[OpsImageCheck] = None  # type: ignore[assignment]
    timeout_seconds: float = 600
    output_cap_bytes: int = 1_000_000
    poll_interval_seconds: float = 2
    github_token: str | None = None
    dockhand_url: str | None = None
    dockhand_token: str | None = None
    dockhand_env: int | None = None
    dockhand_verify_timeout_seconds: float = 60
    dockhand_verify_interval_seconds: float = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "runners", self.runners or {})
        object.__setattr__(self, "scheduled_tasks", self.scheduled_tasks or [])
        object.__setattr__(self, "ops_image_checks", self.ops_image_checks or [])

    @classmethod
    def from_env(cls) -> "Settings":
        raw_runners = os.getenv("TASK_RUNNER_RUNNERS", "{}")
        runners = json.loads(raw_runners)
        if not isinstance(runners, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in runners.items()
        ):
            raise ValueError("TASK_RUNNER_RUNNERS must be a JSON object of name to URL")
        raw_scheduled_tasks = os.getenv("TASK_RUNNER_SCHEDULED_TASKS", "[]")
        scheduled_task_values = json.loads(raw_scheduled_tasks)
        if not isinstance(scheduled_task_values, list) or not all(
            isinstance(value, dict) for value in scheduled_task_values
        ):
            raise ValueError("TASK_RUNNER_SCHEDULED_TASKS must be a JSON array of scheduled task objects")
        raw_ops_image_checks = os.getenv("TASK_RUNNER_OPS_IMAGE_CHECKS", "[]")
        ops_image_check_values = json.loads(raw_ops_image_checks)
        if not isinstance(ops_image_check_values, list) or not all(
            isinstance(value, dict) for value in ops_image_check_values
        ):
            raise ValueError("TASK_RUNNER_OPS_IMAGE_CHECKS must be a JSON array of ops image check objects")
        raw_dockhand_env = os.getenv("TASK_RUNNER_DOCKHAND_ENV")
        dockhand_env = int(raw_dockhand_env) if raw_dockhand_env else None
        return cls(
            database_path=os.getenv("TASK_RUNNER_DATABASE", "/data/tasks.db"),
            runners=runners,
            scheduled_tasks=[ScheduledTask.from_dict(value) for value in scheduled_task_values],
            ops_image_checks=[OpsImageCheck.from_dict(value) for value in ops_image_check_values],
            timeout_seconds=float(os.getenv("TASK_RUNNER_TIMEOUT_SECONDS", "600")),
            output_cap_bytes=int(os.getenv("TASK_RUNNER_OUTPUT_CAP_BYTES", "1000000")),
            poll_interval_seconds=float(os.getenv("TASK_RUNNER_POLL_INTERVAL_SECONDS", "2")),
            github_token=os.getenv("GITHUB_TOKEN"),
            dockhand_url=os.getenv("TASK_RUNNER_DOCKHAND_URL"),
            dockhand_token=os.getenv("TASK_RUNNER_DOCKHAND_TOKEN"),
            dockhand_env=dockhand_env,
            dockhand_verify_timeout_seconds=float(
                os.getenv("TASK_RUNNER_DOCKHAND_VERIFY_TIMEOUT_SECONDS", "60")
            ),
            dockhand_verify_interval_seconds=float(
                os.getenv("TASK_RUNNER_DOCKHAND_VERIFY_INTERVAL_SECONDS", "2")
            ),
        )
