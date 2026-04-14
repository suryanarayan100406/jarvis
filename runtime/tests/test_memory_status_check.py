"""Tests for P5-T7 status check command and summary renderer."""

from __future__ import annotations

import unittest

from runtime.memory import OpenLoopTaskRegister, StatusCheckCommand, StatusCheckCommandError


class StatusCheckCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.register = OpenLoopTaskRegister()
        self.command = StatusCheckCommand(self.register)

    def test_status_check_renders_open_loop_summary(self) -> None:
        self.register.create_task(
            task_id="task-1",
            title="Patch auth service",
            owner_id="ops",
            priority="critical",
            due_at="2026-04-14T08:00:00Z",
        )
        self.register.create_task(
            task_id="task-2",
            title="Collect rollout metrics",
            owner_id="ops",
            priority="high",
        )
        self.register.update_task("task-2", status="in_progress")

        response = self.command.execute(
            actor_id="boss",
            owner_id="ops",
            reference_time="2026-04-14T09:00:00Z",
        )

        self.assertEqual(response.actor_id, "boss")
        self.assertEqual(response.scope_owner_id, "ops")
        self.assertEqual(response.metrics.total, 2)
        self.assertEqual(response.metrics.open, 1)
        self.assertEqual(response.metrics.in_progress, 1)
        self.assertEqual(response.metrics.critical_open, 1)
        self.assertEqual(response.metrics.overdue_open, 1)
        self.assertIn("2 open loops", response.summary)
        self.assertIn("Top open loops:", response.summary)
        self.assertIn("[critical/open] Patch auth service", response.summary)

    def test_status_check_excludes_closed_by_default(self) -> None:
        self.register.create_task(task_id="task-open", title="Open task", owner_id="ops")
        self.register.create_task(task_id="task-done", title="Done task", owner_id="ops")
        self.register.update_task("task-done", status="completed")

        response = self.command.execute(owner_id="ops")

        self.assertEqual(response.metrics.total, 1)
        self.assertEqual(len(response.tasks), 1)
        self.assertEqual(response.tasks[0].task_id, "task-open")

    def test_status_check_can_include_closed_tasks(self) -> None:
        self.register.create_task(task_id="task-completed", title="Done", owner_id="ops")
        self.register.update_task("task-completed", status="completed")

        response = self.command.execute(owner_id="ops", include_completed=True)

        self.assertEqual(response.metrics.total, 1)
        self.assertEqual(response.metrics.completed, 1)
        self.assertIn("no open loops", response.summary.lower())
        self.assertIn("Closed tasks: 1", response.summary)

    def test_status_check_no_tasks_returns_empty_summary(self) -> None:
        response = self.command.execute(owner_id="nobody")

        self.assertEqual(response.metrics.total, 0)
        self.assertIn("no tracked tasks", response.summary.lower())

    def test_status_check_limit_controls_top_lines(self) -> None:
        self.register.create_task(task_id="task-1", title="Task one", owner_id="ops", priority="critical")
        self.register.create_task(task_id="task-2", title="Task two", owner_id="ops", priority="high")

        response = self.command.execute(owner_id="ops", limit=1)
        summary_lines = response.summary.splitlines()
        numbered = [line for line in summary_lines if line.startswith("1.") or line.startswith("2.")]

        self.assertEqual(len(numbered), 1)
        self.assertTrue(numbered[0].startswith("1."))

    def test_status_check_rejects_invalid_limit(self) -> None:
        with self.assertRaises(StatusCheckCommandError):
            self.command.execute(limit=0)


if __name__ == "__main__":
    unittest.main()
