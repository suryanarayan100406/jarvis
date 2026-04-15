"""Tests for P12-T9 status-check accuracy for open-loop summaries."""

from __future__ import annotations

import unittest

from runtime.memory import OpenLoopTaskRegister, StatusCheckCommand


class StatusCheckAccuracyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.register = OpenLoopTaskRegister()
        self.command = StatusCheckCommand(self.register)

    def test_metrics_are_accurate_for_owner_scope_with_closed_included(self) -> None:
        self.register.create_task(
            task_id="ops-open-critical-overdue",
            title="Patch auth service",
            owner_id="ops",
            priority="critical",
            due_at="2026-04-15T08:00:00Z",
        )
        self.register.create_task(
            task_id="ops-progress",
            title="Collect rollout metrics",
            owner_id="ops",
            priority="high",
            due_at="2026-04-15T12:00:00Z",
        )
        self.register.create_task(
            task_id="ops-blocked",
            title="Await security approval",
            owner_id="ops",
            priority="medium",
        )
        self.register.create_task(
            task_id="ops-done",
            title="Finalize migration plan",
            owner_id="ops",
            priority="low",
        )
        self.register.create_task(
            task_id="ops-cancelled",
            title="Deprecated fallback task",
            owner_id="ops",
            priority="low",
        )
        self.register.create_task(
            task_id="other-owner-open",
            title="External owner task",
            owner_id="other",
            priority="critical",
        )

        self.register.update_task("ops-progress", status="in_progress")
        self.register.update_task("ops-blocked", status="blocked")
        self.register.update_task("ops-done", status="completed")
        self.register.update_task("ops-cancelled", status="cancelled")

        response = self.command.execute(
            owner_id="ops",
            include_completed=True,
            reference_time="2026-04-15T09:00:00Z",
            limit=5,
        )

        self.assertEqual(response.metrics.total, 5)
        self.assertEqual(response.metrics.open, 1)
        self.assertEqual(response.metrics.in_progress, 1)
        self.assertEqual(response.metrics.blocked, 1)
        self.assertEqual(response.metrics.completed, 1)
        self.assertEqual(response.metrics.cancelled, 1)
        self.assertEqual(response.metrics.critical_open, 1)
        self.assertEqual(response.metrics.overdue_open, 1)
        self.assertIn("3 open loops (open 1, in_progress 1, blocked 1)", response.summary)
        self.assertIn("Closed in scope: 2", response.summary)

    def test_summary_top_open_loop_order_matches_register_ordering(self) -> None:
        self.register.create_task(task_id="task-crit", title="Critical open", owner_id="ops", priority="critical")
        self.register.create_task(task_id="task-high", title="High open", owner_id="ops", priority="high")
        self.register.create_task(task_id="task-med", title="Medium open", owner_id="ops", priority="medium")
        self.register.create_task(task_id="task-low", title="Low open", owner_id="ops", priority="low")

        response = self.command.execute(owner_id="ops", limit=3)
        expected = self.register.list_tasks(owner_id="ops", include_closed=False)[:3]

        lines = [
            line
            for line in response.summary.splitlines()
            if line.startswith("1.") or line.startswith("2.") or line.startswith("3.")
        ]
        self.assertEqual(len(lines), 3)
        self.assertIn(expected[0].title, lines[0])
        self.assertIn(expected[1].title, lines[1])
        self.assertIn(expected[2].title, lines[2])

    def test_include_completed_false_matches_visible_task_set(self) -> None:
        self.register.create_task(task_id="task-open", title="Open", owner_id="ops", priority="high")
        self.register.create_task(task_id="task-done", title="Done", owner_id="ops", priority="medium")
        self.register.create_task(task_id="task-cancel", title="Cancel", owner_id="ops", priority="low")
        self.register.update_task("task-done", status="completed")
        self.register.update_task("task-cancel", status="cancelled")

        response = self.command.execute(owner_id="ops", include_completed=False)
        expected_open = self.register.list_tasks(owner_id="ops", include_closed=False)

        self.assertEqual([task.task_id for task in response.tasks], [task.task_id for task in expected_open])
        self.assertEqual(response.metrics.total, len(expected_open))
        self.assertNotIn("Closed in scope", response.summary)

    def test_overdue_count_uses_reference_time_boundary(self) -> None:
        self.register.create_task(
            task_id="due-before",
            title="Due before",
            owner_id="ops",
            priority="high",
            due_at="2026-04-15T08:59:59Z",
        )
        self.register.create_task(
            task_id="due-exact",
            title="Due exact",
            owner_id="ops",
            priority="high",
            due_at="2026-04-15T09:00:00Z",
        )
        self.register.create_task(
            task_id="due-after",
            title="Due after",
            owner_id="ops",
            priority="high",
            due_at="2026-04-15T09:00:01Z",
        )

        response = self.command.execute(owner_id="ops", reference_time="2026-04-15T09:00:00Z")

        self.assertEqual(response.metrics.overdue_open, 1)
        self.assertIn("Overdue open: 1", response.summary)


if __name__ == "__main__":
    unittest.main()
