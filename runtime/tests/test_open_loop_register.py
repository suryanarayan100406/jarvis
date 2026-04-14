"""Tests for P5-T6 open-loop task register service."""

from __future__ import annotations

import unittest

from runtime.memory import OpenLoopRegisterError, OpenLoopTaskRegister


class OpenLoopTaskRegisterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.register = OpenLoopTaskRegister()

    def test_create_task_sets_defaults_and_event(self) -> None:
        task = self.register.create_task(
            task_id="task-1",
            title="Investigate latency",
            description="Analyze p95 spike in auth service",
            owner_id="ops",
            tags=["Latency", "Investigate"],
        )

        self.assertEqual(task.task_id, "task-1")
        self.assertEqual(task.status, "open")
        self.assertEqual(task.priority, "medium")
        self.assertEqual(task.version, 1)
        self.assertEqual(task.tags, ("investigate", "latency"))

        events = self.register.get_events("task-1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "created")
        self.assertEqual(events[0].version, 1)

    def test_update_task_increments_version_and_records_fields(self) -> None:
        self.register.create_task(task_id="task-2", title="Prepare patch", owner_id="dev")

        updated = self.register.update_task(
            "task-2",
            status="in_progress",
            priority="high",
            note="Assigned to primary engineer",
        )

        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.status, "in_progress")
        self.assertEqual(updated.priority, "high")

        events = self.register.get_events("task-2")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1].event_type, "updated")
        self.assertEqual(set(events[-1].changed_fields), {"priority", "status"})
        self.assertEqual(events[-1].note, "Assigned to primary engineer")

    def test_list_tasks_filters_and_excludes_closed_by_default(self) -> None:
        self.register.create_task(task_id="task-a", title="A", owner_id="alice", priority="critical")
        self.register.create_task(task_id="task-b", title="B", owner_id="bob", priority="high")
        self.register.create_task(task_id="task-c", title="C", owner_id="alice", priority="low")

        self.register.update_task("task-b", status="completed")

        visible = self.register.list_tasks()
        self.assertEqual([task.task_id for task in visible], ["task-a", "task-c"])

        alice_only = self.register.list_tasks(owner_id="alice")
        self.assertEqual([task.task_id for task in alice_only], ["task-a", "task-c"])

        include_closed = self.register.list_tasks(include_closed=True)
        self.assertEqual([task.task_id for task in include_closed], ["task-a", "task-b", "task-c"])

    def test_snapshot_counts_status_critical_and_overdue(self) -> None:
        self.register.create_task(
            task_id="task-open",
            title="Open",
            owner_id="ops",
            priority="critical",
            due_at="2026-04-14T08:00:00Z",
        )
        self.register.create_task(task_id="task-progress", title="Progress", owner_id="ops", priority="high")
        self.register.create_task(task_id="task-blocked", title="Blocked", owner_id="ops", priority="medium")
        self.register.create_task(task_id="task-completed", title="Completed", owner_id="ops", priority="low")
        self.register.create_task(task_id="task-cancelled", title="Cancelled", owner_id="ops", priority="low")

        self.register.update_task("task-progress", status="in_progress")
        self.register.update_task("task-blocked", status="blocked")
        self.register.update_task("task-completed", status="completed")
        self.register.update_task("task-cancelled", status="cancelled")

        snapshot = self.register.snapshot(reference_time="2026-04-14T09:00:00Z")

        self.assertEqual(snapshot.total, 5)
        self.assertEqual(snapshot.open, 1)
        self.assertEqual(snapshot.in_progress, 1)
        self.assertEqual(snapshot.blocked, 1)
        self.assertEqual(snapshot.completed, 1)
        self.assertEqual(snapshot.cancelled, 1)
        self.assertEqual(snapshot.critical_open, 1)
        self.assertEqual(snapshot.overdue_open, 1)

    def test_remove_marks_task_cancelled_and_returns_false_for_unknown(self) -> None:
        self.register.create_task(task_id="task-remove", title="Remove me", owner_id="ops")

        removed = self.register.remove_task("task-remove", note="obsolete")
        self.assertTrue(removed)

        task = self.register.get_task("task-remove")
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(task.version, 2)
        self.assertFalse(self.register.remove_task("missing"))

    def test_raises_for_invalid_priority_or_status(self) -> None:
        with self.assertRaises(OpenLoopRegisterError):
            self.register.create_task(task_id="bad-priority", title="Bad", owner_id="ops", priority="urgent")

        self.register.create_task(task_id="task-valid", title="Valid", owner_id="ops")
        with self.assertRaises(OpenLoopRegisterError):
            self.register.update_task("task-valid", status="paused")


if __name__ == "__main__":
    unittest.main()
