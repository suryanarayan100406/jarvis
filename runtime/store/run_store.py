"""Local SQLite run store with migrations and event indexing."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    goal: str
    actor_id: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RunEvent:
    event_id: int
    run_id: str
    event_type: str
    payload: dict[str, Any]
    severity: str
    created_at: str


class LocalRunStore:
    """Stores run metadata and events with migration-driven schema management."""

    def __init__(self, db_path: str | Path, migrations_dir: str | Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrations_dir = Path(migrations_dir) if migrations_dir else Path(__file__).parent / "migrations"

    def apply_migrations(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )

            applied = {
                row[0]
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }

            for migration_file in sorted(self.migrations_dir.glob("*.sql")):
                version = migration_file.name
                if version in applied:
                    continue
                script = migration_file.read_text(encoding="utf-8")
                conn.executescript(script)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                    (version, _utc_now_iso()),
                )

    def create_run(self, run_id: str, goal: str, actor_id: str, status: str = "created") -> None:
        now = _utc_now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO runs(run_id, goal, actor_id, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (run_id, goal, actor_id, status, now, now),
            )

    def update_run_status(self, run_id: str, status: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (status, _utc_now_iso(), run_id),
            )

    def get_run(self, run_id: str) -> RunRecord:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT run_id, goal, actor_id, status, created_at, updated_at FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Run not found: {run_id}")

            return RunRecord(
                run_id=row[0],
                goal=row[1],
                actor_id=row[2],
                status=row[3],
                created_at=row[4],
                updated_at=row[5],
            )

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any], severity: str = "info") -> int:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO run_events(run_id, event_type, payload_json, severity, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (run_id, event_type, json.dumps(payload, sort_keys=True), severity, _utc_now_iso()),
            )
            return int(cursor.lastrowid)

    def list_events(self, run_id: str, limit: int | None = 100) -> list[RunEvent]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")

        with self._connection() as conn:
            if limit is None:
                rows = conn.execute(
                    """
                    SELECT event_id, run_id, event_type, payload_json, severity, created_at
                    FROM run_events
                    WHERE run_id = ?
                    ORDER BY created_at ASC, event_id ASC
                    """,
                    (run_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT event_id, run_id, event_type, payload_json, severity, created_at
                    FROM run_events
                    WHERE run_id = ?
                    ORDER BY created_at ASC, event_id ASC
                    LIMIT ?
                    """,
                    (run_id, limit),
                ).fetchall()

            return [
                RunEvent(
                    event_id=row[0],
                    run_id=row[1],
                    event_type=row[2],
                    payload=json.loads(row[3]),
                    severity=row[4],
                    created_at=row[5],
                )
                for row in rows
            ]

    def list_applied_migrations(self) -> list[str]:
        with self._connection() as conn:
            rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version ASC").fetchall()
            return [row[0] for row in rows]

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
