"""Tests for P2-T9 run replay endpoint."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.replay import RunReplayEndpoint, RunReplayNotFoundError
from runtime.store import LocalRunStore


class RunReplayEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runs.db"
        self.store = LocalRunStore(self.db_path)
        self.store.apply_migrations()

        self.store.create_run("run-1", "Collect diagnostics", "boss")
        self.store.append_event("run-1", "runtime.plan.completed", {"task_count": 2}, severity="info")
        self.store.append_event("run-1", "runtime.execute.completed", {"status": "success"}, severity="warning")
        self.store.append_event("run-1", "runtime.report.completed", {"report_id": "rpt-1"}, severity="info")

        self.endpoint = RunReplayEndpoint(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_replay_returns_ordered_timeline_with_metadata(self) -> None:
        replay = self.endpoint.replay("run-1")

        self.assertEqual(replay.run.run_id, "run-1")
        self.assertEqual(
            [event.event_type for event in replay.events],
            [
                "runtime.plan.completed",
                "runtime.execute.completed",
                "runtime.report.completed",
            ],
        )
        self.assertGreaterEqual(replay.events[0].offset_ms, 0)
        self.assertEqual(replay.metadata["returned_event_count"], 3)
        self.assertFalse(replay.metadata["truncated"])
        self.assertEqual(len(replay.metadata["audit_digest"]), 64)

    def test_replay_filters_event_types_and_severities(self) -> None:
        replay = self.endpoint.replay(
            "run-1",
            event_types={"runtime.execute.completed", "runtime.report.completed"},
            severities={"warning"},
        )

        self.assertEqual(len(replay.events), 1)
        self.assertEqual(replay.events[0].event_type, "runtime.execute.completed")
        self.assertEqual(replay.events[0].severity, "warning")
        self.assertEqual(replay.metadata["filtered_event_count"], 1)

    def test_replay_limit_sets_truncation_and_cursor(self) -> None:
        replay = self.endpoint.replay("run-1", limit=2)

        self.assertEqual(len(replay.events), 2)
        self.assertTrue(replay.metadata["truncated"])
        self.assertEqual(replay.metadata["filtered_event_count"], 3)
        self.assertEqual(replay.metadata["next_cursor_event_id"], replay.events[-1].event_id)

    def test_replay_can_redact_payloads(self) -> None:
        replay = self.endpoint.replay("run-1", include_payload=False)

        self.assertEqual(replay.events[0].payload, {"redacted": True})
        self.assertTrue(replay.metadata["include_payload"] is False)

    def test_missing_run_raises_not_found(self) -> None:
        with self.assertRaises(RunReplayNotFoundError):
            self.endpoint.replay("run-missing")

    def test_invalid_limit_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self.endpoint.replay("run-1", limit=0)


if __name__ == "__main__":
    unittest.main()
