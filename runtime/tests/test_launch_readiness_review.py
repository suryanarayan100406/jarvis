"""Tests for P11-T12 launch readiness review and sign-off workflow."""

from __future__ import annotations

import unittest

from runtime.orchestration import (
    BackupStrategyManager,
    CanaryObservation,
    DisasterRecoveryRunbookManager,
    ErrorBudgetMonitor,
    FailureInjectionDrillRunner,
    LaunchChecklistManager,
    LaunchReadinessReviewError,
    LaunchReadinessReviewWorkflow,
    OperationalHealthMetric,
    OperationsHealthDashboardBuilder,
    RecoveryStepObservation,
    ReleasePipelineManager,
    RestoreWorkflowEngine,
    SLOObservation,
    build_default_core_slo_catalog,
    build_default_disaster_recovery_runbook,
    build_default_operator_runbook_bundle,
    default_failure_injection_scenarios,
)
from runtime.security.operator_drill_scripts import OperatorReadinessReport


class LaunchReadinessReviewWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = LaunchReadinessReviewWorkflow()
        self.catalog = build_default_core_slo_catalog()
        self.launch_checklist = self._build_launch_checklist(warning=False)
        self.disaster_recovery_result = self._build_disaster_recovery_result()
        self.release_pipeline = self._build_release_pipeline(promoted=True)
        self.failure_report = self._build_failure_report()
        self.runbook_bundle = build_default_operator_runbook_bundle()

    def test_create_review_recommends_approve_when_all_signals_pass(self) -> None:
        review = self.workflow.create_review(
            launch_checklist=self.launch_checklist,
            disaster_recovery_result=self.disaster_recovery_result,
            release_pipeline=self.release_pipeline,
            failure_injection_report=self.failure_report,
            operator_runbook_bundle=self.runbook_bundle,
        )

        self.assertEqual(review.status, "open")
        self.assertEqual(review.recommendation, "approve")
        self.assertEqual(review.required_signoff_roles, ("primary_user", "authorized_operator"))
        self.assertTrue(all(item.status == "pass" for item in review.checklist))

    def test_create_review_recommends_hold_when_warning_signal_present(self) -> None:
        warning_checklist = self._build_launch_checklist(warning=True)

        review = self.workflow.create_review(
            launch_checklist=warning_checklist,
            disaster_recovery_result=self.disaster_recovery_result,
            release_pipeline=self.release_pipeline,
            failure_injection_report=self.failure_report,
            operator_runbook_bundle=self.runbook_bundle,
        )

        self.assertEqual(review.recommendation, "hold")
        self.assertTrue(any(item.status == "warn" for item in review.checklist))

    def test_finalize_review_requires_required_signoff_roles(self) -> None:
        review = self.workflow.create_review(
            launch_checklist=self.launch_checklist,
            disaster_recovery_result=self.disaster_recovery_result,
            release_pipeline=self.release_pipeline,
            failure_injection_report=self.failure_report,
            operator_runbook_bundle=self.runbook_bundle,
        )

        review = self.workflow.add_signoff(
            review.review_id,
            reviewer_id="primary-reviewer",
            reviewer_role="primary_user",
        )

        with self.assertRaises(LaunchReadinessReviewError):
            self.workflow.finalize_review(
                review.review_id,
                decision="approve",
                reviewer_id="primary-reviewer",
                reviewer_role="primary_user",
                note="missing operator signoff",
            )

        review = self.workflow.add_signoff(
            review.review_id,
            reviewer_id="ops-reviewer",
            reviewer_role="authorized_operator",
        )
        finalized = self.workflow.finalize_review(
            review.review_id,
            decision="approve",
            reviewer_id="ops-reviewer",
            reviewer_role="authorized_operator",
            note="all launch signoffs complete",
        )

        self.assertEqual(finalized.status, "approved")

    def test_finalize_review_blocks_approve_without_override_when_not_recommended(self) -> None:
        review = self.workflow.create_review(
            launch_checklist=self._build_launch_checklist(warning=True),
            disaster_recovery_result=self.disaster_recovery_result,
            release_pipeline=self.release_pipeline,
            failure_injection_report=self.failure_report,
            operator_runbook_bundle=self.runbook_bundle,
        )
        review = self.workflow.add_signoff(
            review.review_id,
            reviewer_id="primary-reviewer",
            reviewer_role="primary_user",
        )

        with self.assertRaises(LaunchReadinessReviewError):
            self.workflow.finalize_review(
                review.review_id,
                decision="approve",
                reviewer_id="primary-reviewer",
                reviewer_role="primary_user",
                note="attempt without override",
            )

        finalized = self.workflow.finalize_review(
            review.review_id,
            decision="approve",
            reviewer_id="primary-reviewer",
            reviewer_role="primary_user",
            note="override granted",
            allow_override=True,
        )
        self.assertEqual(finalized.status, "approved")

    def test_review_manifest_is_deterministic(self) -> None:
        review = self.workflow.create_review(
            launch_checklist=self.launch_checklist,
            disaster_recovery_result=self.disaster_recovery_result,
            release_pipeline=self.release_pipeline,
            failure_injection_report=self.failure_report,
            operator_runbook_bundle=self.runbook_bundle,
        )

        first = review.to_manifest()
        second = review.to_manifest()
        self.assertEqual(first, second)

    def _build_launch_checklist(self, *, warning: bool):
        catalog_report = ErrorBudgetMonitor().evaluate_catalog(
            self.catalog,
            [
                SLOObservation(
                    slo_id=slo.slo_id,
                    total_events=1000,
                    compliant_events=1000,
                    elapsed_days=slo.window_days,
                    metadata={},
                )
                for slo in self.catalog.slos
            ],
        )

        score_modifier = 0.95 if warning else 1.0
        health_dashboard = OperationsHealthDashboardBuilder().build_dashboard(
            {
                "runtime": [
                    OperationalHealthMetric(
                        metric_id="runtime",
                        value=round(0.99 * score_modifier, 6),
                        target=0.99,
                        direction="higher_is_better",
                        warning_floor=0.96,
                        critical_floor=0.9,
                        weight=1.0,
                        metadata={},
                    )
                ],
                "autonomy": [
                    OperationalHealthMetric(
                        metric_id="autonomy",
                        value=round(0.985 * score_modifier, 6),
                        target=0.985,
                        direction="higher_is_better",
                        warning_floor=0.96,
                        critical_floor=0.9,
                        weight=1.0,
                        metadata={},
                    )
                ],
                "security": [
                    OperationalHealthMetric(
                        metric_id="security",
                        value=round(0.995 * score_modifier, 6),
                        target=0.995,
                        direction="higher_is_better",
                        warning_floor=0.97,
                        critical_floor=0.92,
                        weight=1.0,
                        metadata={},
                    )
                ],
            },
            window_id="launch-review-window",
            metadata={},
        )

        dr_result = self._build_disaster_recovery_result()
        pipeline = self._build_release_pipeline(promoted=True)
        readiness = OperatorReadinessReport(
            started_at="2026-05-10T00:00:00Z",
            finished_at="2026-05-10T00:05:00Z",
            total_drills=3,
            passed=3,
            degraded=0,
            failed=0,
            readiness_score=1.0,
            summary="ready",
            drills=(),
        )

        return LaunchChecklistManager().build_checklist(
            error_budget_report=catalog_report,
            health_dashboard=health_dashboard,
            disaster_recovery_result=dr_result,
            release_pipeline=pipeline,
            operator_readiness_report=readiness,
        )

    @staticmethod
    def _build_disaster_recovery_result():
        runbook = build_default_disaster_recovery_runbook()
        observations = [
            RecoveryStepObservation(
                step_id=target.step_id,
                actual_duration_minutes=max(0.1, target.target_rto_minutes - 1.0),
                observed_data_loss_minutes=max(0.0, target.target_rpo_minutes - 0.5),
                status="completed",
                details=None,
                metadata={},
            )
            for target in runbook.recovery_windows
        ]
        return DisasterRecoveryRunbookManager().evaluate_drill(runbook, observations, strict=True)

    @staticmethod
    def _build_release_pipeline(*, promoted: bool):
        manager = ReleasePipelineManager()
        pipeline = manager.create_pipeline(
            release_id="release-2026-09-01",
            previous_release_id="release-2026-08-31",
            canary_percentage=10,
        )
        pipeline = manager.evaluate_canary(
            pipeline.pipeline_id,
            CanaryObservation(
                request_count=1500,
                error_count=12 if promoted else 120,
                p95_latency_ms=180.0 if promoted else 420.0,
                metadata={},
            ),
        )
        return pipeline

    @staticmethod
    def _build_failure_report():
        handler = lambda scenario, _context: {
            "status": "contained",
            "recovered": True,
            "response_seconds": min(8.0, scenario.target_response_seconds),
            "detection_seconds": 2.0,
        }
        runner = FailureInjectionDrillRunner(
            handlers={
                "orchestration": handler,
                "memory": handler,
                "configuration": handler,
                "security": handler,
                "release_pipeline": handler,
            }
        )
        return runner.run_drills(default_failure_injection_scenarios())


if __name__ == "__main__":
    unittest.main()
