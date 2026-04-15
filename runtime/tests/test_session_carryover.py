"""Tests for P12-T2 previous-session carry-over summary workflow."""

from __future__ import annotations

import unittest

from runtime.memory import OpenLoopTaskRegister
from runtime.session import (
    PreviousSessionCarryOverWorkflow,
    SessionCarryOverError,
)


class PreviousSessionCarryOverWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.register = OpenLoopTaskRegister()
        self.workflow = PreviousSessionCarryOverWorkflow(self.register)

    def test_build_summary_includes_open_loop_and_context_notes(self) -> None:
        self.register.create_task(
            task_id="loop-1",
            title="Investigate API latency",
            owner_id="ops",
            priority="critical",
            due_at="2026-04-15T10:00:00Z",
        )
        self.register.create_task(
            task_id="loop-2",
            title="Prepare rollback plan",
            owner_id="ops",
            priority="high",
        )
        self.register.update_task("loop-2", status="in_progress")

        summary = self.workflow.build_summary(
            previous_session_id="session-0415",
            owner_id="ops",
            context_notes=["Release canary reached 15% traffic", "Awaiting signoff from operator"],
            reference_time="2026-04-15T12:00:00Z",
        )

        self.assertEqual(summary.open_loop_count, 1)
        self.assertEqual(summary.in_progress_count, 1)
        self.assertEqual(summary.blocked_count, 0)
        self.assertEqual(summary.critical_count, 1)
        self.assertEqual(summary.overdue_count, 1)
        self.assertEqual(len(summary.carry_over_items), 2)
        self.assertIn("Carry-over summary from session-0415.", summary.summary_text)
        self.assertIn("Context notes:", summary.summary_text)

    def test_build_summary_handles_empty_scope(self) -> None:
        summary = self.workflow.build_summary(
            previous_session_id="session-empty",
            owner_id="ops",
        )

        self.assertEqual(summary.open_loop_count, 0)
        self.assertEqual(len(summary.carry_over_items), 0)
        self.assertIn("No active carry-over items.", summary.summary_text)

    def test_build_summary_limit_restricts_items(self) -> None:
        for index in range(0, 3):
            self.register.create_task(
                task_id=f"loop-{index + 1}",
                title=f"Task {index + 1}",
                owner_id="ops",
                priority="high",
            )

        summary = self.workflow.build_summary(
            previous_session_id="session-limited",
            owner_id="ops",
            limit=2,
        )

        self.assertEqual(len(summary.carry_over_items), 2)

    def test_invalid_previous_session_id_raises(self) -> None:
        with self.assertRaises(SessionCarryOverError):
            self.workflow.build_summary(previous_session_id="   ")

    def test_manifest_is_deterministic(self) -> None:
        self.register.create_task(
            task_id="loop-1",
            title="Investigate API latency",
            owner_id="ops",
            priority="high",
        )

        summary = self.workflow.build_summary(
            previous_session_id="session-deterministic",
            owner_id="ops",
            context_notes=["resume from previous checkpoint"],
        )

        first = summary.to_manifest()
        second = summary.to_manifest()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
