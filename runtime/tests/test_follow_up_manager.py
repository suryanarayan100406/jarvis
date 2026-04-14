"""Tests for P6-T7 follow-up manager."""

from __future__ import annotations

import unittest

from runtime.orchestration import FollowUpManager, FollowUpManagerError


class FollowUpManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = FollowUpManager()

    def test_create_and_list_pending_items(self) -> None:
        self.manager.create_item(
            follow_up_id="fu-1",
            title="Investigate host latency",
            owner_id="ops",
            source_type="runbook",
            source_id="rb-ops",
            priority="high",
            due_at="2026-04-16T10:00:00Z",
        )
        self.manager.create_item(
            follow_up_id="fu-2",
            title="Confirm rollback plan",
            owner_id="ops",
            source_type="escalation",
            source_id="esc-2",
            priority="critical",
            due_at="2026-04-16T09:00:00Z",
        )

        listed = self.manager.list_items(owner_id="ops")

        self.assertEqual(len(listed), 2)
        self.assertEqual([item.follow_up_id for item in listed], ["fu-2", "fu-1"])

    def test_status_transition_and_completion(self) -> None:
        self.manager.create_item(
            follow_up_id="fu-3",
            title="Collect logs",
            owner_id="ops",
            source_type="runbook",
            source_id="rb-3",
        )

        in_progress = self.manager.update_status("fu-3", status="in_progress")
        completed = self.manager.update_status("fu-3", status="completed", metadata_patch={"resolution": "done"})

        self.assertEqual(in_progress.status, "in_progress")
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.metadata["resolution"], "done")
        self.assertEqual(len(self.manager.list_items()), 0)

    def test_snooze_updates_due_date_and_reason(self) -> None:
        self.manager.create_item(
            follow_up_id="fu-4",
            title="Review alert",
            owner_id="ops",
            source_type="event",
            source_id="evt-1",
            due_at="2026-04-16T09:00:00Z",
        )

        snoozed = self.manager.snooze("fu-4", due_at="2026-04-16T12:00:00Z", reason="waiting for logs")

        self.assertEqual(snoozed.due_at, "2026-04-16T12:00:00Z")
        self.assertEqual(snoozed.metadata["snooze_reason"], "waiting for logs")

    def test_overdue_and_snapshot_metrics(self) -> None:
        self.manager.create_item(
            follow_up_id="fu-5",
            title="Old issue",
            owner_id="ops",
            source_type="event",
            source_id="evt-old",
            due_at="2026-04-16T08:00:00Z",
        )
        self.manager.create_item(
            follow_up_id="fu-6",
            title="Future issue",
            owner_id="ops",
            source_type="event",
            source_id="evt-future",
            due_at="2026-04-16T12:00:00Z",
        )

        overdue = self.manager.list_overdue(reference_time="2026-04-16T10:00:00Z")
        snapshot = self.manager.snapshot(reference_time="2026-04-16T10:00:00Z")

        self.assertEqual(len(overdue), 1)
        self.assertEqual(overdue[0].follow_up_id, "fu-5")
        self.assertEqual(snapshot.total, 2)
        self.assertEqual(snapshot.overdue, 1)

    def test_invalid_transition_from_closed_item_raises(self) -> None:
        self.manager.create_item(
            follow_up_id="fu-7",
            title="Already closed",
            owner_id="ops",
            source_type="event",
            source_id="evt-closed",
        )
        self.manager.update_status("fu-7", status="completed")

        with self.assertRaises(FollowUpManagerError):
            self.manager.update_status("fu-7", status="open")

        with self.assertRaises(FollowUpManagerError):
            self.manager.snooze("fu-7", due_at="2026-04-16T13:00:00Z")

    def test_invalid_priority_raises(self) -> None:
        with self.assertRaises(FollowUpManagerError):
            self.manager.create_item(
                title="bad",
                owner_id="ops",
                source_type="event",
                source_id="evt",
                priority="urgent",
            )


if __name__ == "__main__":
    unittest.main()
