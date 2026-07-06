import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str = "/data/tasks.db"
    runners: dict[str, str] = None  # type: ignore[assignment]
    timeout_seconds: float = 600
    output_cap_bytes: int = 1_000_000
    poll_interval_seconds: float = 2
    github_token: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "runners", self.runners or {})

    @classmethod
    def from_env(cls) -> "Settings":
        raw = os.getenv("TASK_RUNNER_RUNNERS", "{}")
        runners = json.loads(raw)
        if not isinstance(runners, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in runners.items()
        ):
            raise ValueError("TASK_RUNNER_RUNNERS must be a JSON object of name to URL")
        return cls(
            database_path=os.getenv("TASK_RUNNER_DATABASE", "/data/tasks.db"),
            runners=runners,
            timeout_seconds=float(os.getenv("TASK_RUNNER_TIMEOUT_SECONDS", "600")),
            output_cap_bytes=int(os.getenv("TASK_RUNNER_OUTPUT_CAP_BYTES", "1000000")),
            poll_interval_seconds=float(os.getenv("TASK_RUNNER_POLL_INTERVAL_SECONDS", "2")),
            github_token=os.getenv("GITHUB_TOKEN"),
        )

