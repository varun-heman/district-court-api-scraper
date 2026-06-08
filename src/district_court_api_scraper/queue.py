from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import (
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCESS,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Task:
    id: int
    run_id: str
    task_type: str
    status: str
    payload: dict[str, Any]
    attempts: int
    dedupe_key: str | None
    last_error: str | None


class TaskQueue:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    dedupe_key TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_dedupe
                    ON tasks(run_id, task_type, dedupe_key)
                    WHERE dedupe_key IS NOT NULL;

                CREATE INDEX IF NOT EXISTS idx_tasks_lookup
                    ON tasks(run_id, status, task_type, priority, id);

                CREATE TABLE IF NOT EXISTS task_attempt_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    ts TEXT NOT NULL,
                    error_type TEXT,
                    captcha_stage TEXT,
                    message TEXT,
                    response_snippet TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                """
            )

    def enqueue(
        self,
        *,
        run_id: str,
        task_type: str,
        payload: dict[str, Any],
        dedupe_key: str | None = None,
        priority: int = 100,
    ) -> int:
        now = _utc_now()
        payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with self._connect() as conn:
            if dedupe_key is not None:
                row = conn.execute(
                    """
                    SELECT id FROM tasks
                    WHERE run_id=? AND task_type=? AND dedupe_key=?
                    LIMIT 1
                    """,
                    (run_id, task_type, dedupe_key),
                ).fetchone()
                if row:
                    return int(row["id"])
            cursor = conn.execute(
                """
                INSERT INTO tasks (
                    run_id, task_type, status, priority, payload_json,
                    dedupe_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_type,
                    STATUS_PENDING,
                    priority,
                    payload_json,
                    dedupe_key,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def claim_next(self, run_id: str, task_types: list[str] | None = None) -> Task | None:
        with self._connect() as conn:
            clauses = ["run_id = ?", "status = ?"]
            params: list[Any] = [run_id, STATUS_PENDING]
            if task_types:
                placeholders = ",".join("?" for _ in task_types)
                clauses.append(f"task_type IN ({placeholders})")
                params.extend(task_types)
            where_sql = " AND ".join(clauses)
            row = conn.execute(
                f"""
                SELECT * FROM tasks
                WHERE {where_sql}
                ORDER BY priority ASC, id ASC
                LIMIT 1
                """
                ,
                params,
            ).fetchone()
            if row is None:
                return None
            now = _utc_now()
            conn.execute(
                """
                UPDATE tasks
                SET status=?, attempts=attempts+1, started_at=?, updated_at=?
                WHERE id=?
                """,
                (STATUS_RUNNING, now, now, row["id"]),
            )
            payload = json.loads(row["payload_json"])
            return Task(
                id=int(row["id"]),
                run_id=str(row["run_id"]),
                task_type=str(row["task_type"]),
                status=STATUS_RUNNING,
                payload=payload,
                attempts=int(row["attempts"]) + 1,
                dedupe_key=row["dedupe_key"],
                last_error=row["last_error"],
            )

    def mark_success(self, task_id: int, result: dict[str, Any] | None = None) -> None:
        now = _utc_now()
        result_json = None if result is None else json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status=?, result_json=?, finished_at=?, updated_at=?
                WHERE id=?
                """,
                (STATUS_SUCCESS, result_json, now, now, task_id),
            )

    def mark_failed(self, task_id: int, message: str) -> None:
        self._mark_terminal(task_id, STATUS_FAILED, message)

    def mark_incomplete(self, task_id: int, message: str) -> None:
        self._mark_terminal(task_id, STATUS_INCOMPLETE, message)

    def _mark_terminal(self, task_id: int, status: str, message: str) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status=?, last_error=?, finished_at=?, updated_at=?
                WHERE id=?
                """,
                (status, message, now, now, task_id),
            )

    def log_attempt(
        self,
        task_id: int,
        *,
        error_type: str,
        message: str,
        captcha_stage: str | None = None,
        response_snippet: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_attempt_logs (
                    task_id, ts, error_type, captcha_stage, message, response_snippet
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    _utc_now(),
                    error_type,
                    captcha_stage,
                    message,
                    (response_snippet or "")[:1200],
                ),
            )

    def get_tasks(
        self,
        run_id: str,
        *,
        status: str | None = None,
        task_type: str | None = None,
    ) -> list[Task]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if task_type:
            clauses.append("task_type = ?")
            params.append(task_type)
        where_sql = " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE {where_sql} ORDER BY id ASC",
                params,
            ).fetchall()
        result: list[Task] = []
        for row in rows:
            result.append(
                Task(
                    id=int(row["id"]),
                    run_id=str(row["run_id"]),
                    task_type=str(row["task_type"]),
                    status=str(row["status"]),
                    payload=json.loads(row["payload_json"]),
                    attempts=int(row["attempts"]),
                    dedupe_key=row["dedupe_key"],
                    last_error=row["last_error"],
                )
            )
        return result

    def requeue_incomplete(self, run_id: str) -> int:
        now = _utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status=?, updated_at=?, started_at=NULL, finished_at=NULL
                WHERE run_id=? AND status=?
                """,
                (STATUS_PENDING, now, run_id, STATUS_INCOMPLETE),
            )
            return int(cursor.rowcount or 0)

