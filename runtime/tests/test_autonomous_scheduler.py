"""Tests for P6-T1 autonomous scheduler trigger engine."""

from __future__ import annotations

import unittest

from runtime.orchestration import AutonomousScheduler, SchedulerError


class AutonomousSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = AutonomousScheduler()

    def test_register_and_list_triggers(self) -> None:
        self.scheduler.register_cron_trigger(
            trigger_id="cron.health",
            name="Health check",
            expression="*/5 * * * *",
            payload={"workflow": "health-check"},
        )
        self.scheduler.register_calendar_trigger(
            trigger_id="cal.report",
            name="Daily report",
            run_at=["2026-04-15T09:00:00Z"],
            payload={"workflow": "daily-report"},
        )

        listed = self.scheduler.list_triggers()

        self.assertEqual(len(listed), 2)
        self.assertEqual([trigger.trigger_id for trigger in listed], ["cal.report", "cron.health"])

    def test_register_rejects_duplicate_trigger_id(self) -> None:
        self.scheduler.register_cron_trigger(
            trigger_id="cron.health",
            name="Health check",
            expression="*/5 * * * *",
        )

        with self.assertRaises(SchedulerError):
            self.scheduler.register_calendar_trigger(
                trigger_id="cron.health",
                name="Conflicting id",
                run_at=["2026-04-15T09:00:00Z"],
            )

    def test_invalid_cron_expression_raises(self) -> None:
        with self.assertRaises(SchedulerError):
            self.scheduler.register_cron_trigger(
                trigger_id="cron.bad",
                name="Invalid",
                expression="* * *",
            )

    def test_poll_due_emits_matching_cron_once_per_minute(self) -> None:
        self.scheduler.register_cron_trigger(
            trigger_id="cron.health",
            name="Health check",
            expression="*/5 * * * *",
            payload={"workflow": "health-check"},
        )

        first = self.scheduler.poll_due(reference_time="2026-04-15T10:15:22Z")
        second = self.scheduler.poll_due(reference_time="2026-04-15T10:15:40Z")
        third = self.scheduler.poll_due(reference_time="2026-04-15T10:16:00Z")

        self.assertEqual(first.due_count, 1)
        self.assertEqual(first.activations[0].scheduled_for, "2026-04-15T10:15:00Z")
        self.assertEqual(second.due_count, 0)
        self.assertEqual(third.due_count, 0)

    def test_poll_due_emits_calendar_triggers_once_when_reached(self) -> None:
        self.scheduler.register_calendar_trigger(
            trigger_id="cal.report",
            name="Daily report",
            run_at=["2026-04-15T09:00:00Z", "2026-04-15T18:00:00Z"],
            payload={"workflow": "daily-report"},
        )

        before = self.scheduler.poll_due(reference_time="2026-04-15T08:59:00Z")
        first_due = self.scheduler.poll_due(reference_time="2026-04-15T09:00:01Z")
        repeated = self.scheduler.poll_due(reference_time="2026-04-15T09:30:00Z")
        second_due = self.scheduler.poll_due(reference_time="2026-04-15T18:00:00Z")

        self.assertEqual(before.due_count, 0)
        self.assertEqual(first_due.due_count, 1)
        self.assertEqual(first_due.activations[0].scheduled_for, "2026-04-15T09:00:00Z")
        self.assertEqual(repeated.due_count, 0)
        self.assertEqual(second_due.due_count, 1)
        self.assertEqual(second_due.activations[0].scheduled_for, "2026-04-15T18:00:00Z")

    def test_disabled_trigger_is_not_emitted(self) -> None:
        self.scheduler.register_cron_trigger(
            trigger_id="cron.health",
            name="Health check",
            expression="*/5 * * * *",
        )
        self.scheduler.set_trigger_enabled("cron.health", False)

        result = self.scheduler.poll_due(reference_time="2026-04-15T10:15:00Z")

        self.assertEqual(result.due_count, 0)

    def test_next_run_at_reports_upcoming_for_cron_and_calendar(self) -> None:
        self.scheduler.register_cron_trigger(
            trigger_id="cron.health",
            name="Health check",
            expression="*/10 * * * *",
        )
        self.scheduler.register_calendar_trigger(
            trigger_id="cal.report",
            name="Daily report",
            run_at=["2026-04-15T09:00:00Z", "2026-04-15T18:00:00Z"],
        )

        next_cron = self.scheduler.next_run_at("cron.health", reference_time="2026-04-15T10:12:00Z")
        next_calendar = self.scheduler.next_run_at("cal.report", reference_time="2026-04-15T10:12:00Z")

        self.assertEqual(next_cron, "2026-04-15T10:20:00Z")
        self.assertEqual(next_calendar, "2026-04-15T18:00:00Z")

    def test_weekday_and_month_aliases_are_supported(self) -> None:
        self.scheduler.register_cron_trigger(
            trigger_id="cron.alias",
            name="Alias schedule",
            expression="0 9 * apr mon",
        )

        match = self.scheduler.poll_due(reference_time="2026-04-20T09:00:01Z")
        miss = self.scheduler.poll_due(reference_time="2026-04-21T09:00:01Z")

        self.assertEqual(match.due_count, 1)
        self.assertEqual(miss.due_count, 0)


if __name__ == "__main__":
    unittest.main()
