import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


TERMINAL_STATES = {"completed", "failed", "timeout"}


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = Lock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, repo TEXT NOT NULL, issue_number INTEGER NOT NULL,
                    runner TEXT NOT NULL, runner_url TEXT NOT NULL, status TEXT NOT NULL,
                    execution_id TEXT, prompt TEXT, result TEXT, log TEXT NOT NULL DEFAULT '',
                    output_truncated INTEGER NOT NULL DEFAULT 0, error TEXT,
                    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
                )
            """)

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        with self._lock, self.connect() as db:
            db.execute(sql, parameters)

    def create_task(self, task_id: str, repo: str, issue_number: int, runner: str, runner_url: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.execute(
            "INSERT INTO tasks (id,repo,issue_number,runner,runner_url,status,created_at) VALUES (?,?,?,?,?,'queued',?)",
            (task_id, repo, issue_number, runner, runner_url, now),
        )

    def update(self, task_id: str, **values: Any) -> None:
        if not values:
            return
        columns = ", ".join(f"{key}=?" for key in values)
        self.execute(f"UPDATE tasks SET {columns} WHERE id=?", (*values.values(), task_id))

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

