"""Tests for P6-T10 autonomous activity summary generation."""

from __future__ import annotations

import unittest

from runtime.orchestration import ActivitySummaryError, AutonomousActivitySummaryGenerator


class AutonomousActivitySummaryGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = AutonomousActivitySummaryGenerator()
        self.generator.record_activity(
            activity_id="a1",
            category="scheduler",
            status="success",
            summary="Scheduled maintenance check completed",
            severity="info",
            occurred_at="2026-04-16T08:00:00Z",
        )
        self.generator.record_activity(
            activity_id="a2",
            category="runbook",
            status="degraded",
            summary="Fallback used for restart step",
            severity="warning",
            occurred_at="2026-04-16T09:00:00Z",
        )
        self.generator.record_activity(
            activity_id="a3",
            category="watchdog",
            status="failed",
            summary="Run terminalized after restart budget exhaustion",
            severity="critical",
            occurred_at="2026-04-16T10:00:00Z",
        )

    def test_generate_daily_summary_aggregates_metrics(self) -> None:
        summary = self.generator.generate_summary(
            period="daily",
            reference_time="2026-04-16T12:00:00Z",
            open_follow_ups=2,
        )

        self.assertEqual(summary.period, "daily")
        self.assertEqual(summary.total_activities, 3)
        self.assertEqual(summary.success_count, 1)
        self.assertEqual(summary.degraded_count, 1)
        self.assertEqual(summary.failed_count, 1)
        self.assertEqual(summary.critical_alerts, 1)
        self.assertEqual(summary.open_follow_ups, 2)
        self.assertIn("Autonomous Activity Summary (daily)", summary.markdown)
        self.assertIn("Critical alerts: 1", summary.markdown)

    def test_generate_weekly_summary_respects_window(self) -> None:
        self.generator.record_activity(
            activity_id="a-old",
            category="scheduler",
            status="success",
            summary="Old record outside weekly window",
            severity="info",
            occurred_at="2026-04-01T10:00:00Z",
        )

        summary = self.generator.generate_summary(
            period="weekly",
            reference_time="2026-04-16T12:00:00Z",
        )

        self.assertEqual(summary.total_activities, 3)
        self.assertNotIn("a-old", summary.markdown)

    def test_top_categories_include_most_frequent(self) -> None:
        self.generator.record_activity(
            activity_id="a4",
            category="runbook",
            status="success",
            summary="Runbook follow-up completed",
            severity="info",
            occurred_at="2026-04-16T11:00:00Z",
        )

        summary = self.generator.generate_summary(
            period="daily",
            reference_time="2026-04-16T12:00:00Z",
        )

        self.assertGreaterEqual(len(summary.top_categories), 1)
        self.assertEqual(summary.top_categories[0], "runbook")

    def test_invalid_period_raises(self) -> None:
        with self.assertRaises(ActivitySummaryError):
            self.generator.generate_summary(period="monthly", reference_time="2026-04-16T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
