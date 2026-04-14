"""Tests for P2-T8 local run store with migrations and indexing."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from runtime.store import LocalRunStore


class LocalRunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runs.db"
        self.store = LocalRunStore(self.db_path)
        self.store.apply_migrations()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_migrations_apply_and_are_idempotent(self) -> None:
        first = self.store.list_applied_migrations()
        self.store.apply_migrations()
        second = self.store.list_applied_migrations()

        self.assertEqual(first, second)
        self.assertIn("001_initial.sql", second)

    def test_create_and_get_run(self) -> None:
        self.store.create_run("run-1", "Collect diagnostics", "boss")

        run = self.store.get_run("run-1")

        self.assertEqual(run.goal, "Collect diagnostics")
        self.assertEqual(run.status, "created")

    def test_append_and_query_events(self) -> None:
        self.store.create_run("run-2", "Collect diagnostics", "boss")
        self.store.append_event("run-2", "runtime.plan.completed", {"ok": True})
        self.store.append_event("run-2", "runtime.execute.completed", {"count": 1})

        events = self.store.list_events("run-2")

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_type, "runtime.plan.completed")
        self.assertEqual(events[1].payload["count"], 1)

    def test_event_indexes_exist(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("PRAGMA index_list('run_events')").fetchall()
            index_names = {row[1] for row in rows}
        finally:
            conn.close()

        self.assertIn("idx_run_events_run_id_created_at", index_names)
        self.assertIn("idx_run_events_event_type", index_names)

    def test_update_run_status(self) -> None:
        self.store.create_run("run-3", "Check health", "boss")
        self.store.update_run_status("run-3", "completed")

        run = self.store.get_run("run-3")
        self.assertEqual(run.status, "completed")


if __name__ == "__main__":
    unittest.main()
