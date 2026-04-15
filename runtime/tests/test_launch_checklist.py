"""Tests for P11-T8 launch checklist automation and gate validation."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.orchestration import (
    CanaryObservation,
    DisasterRecoveryRunbookManager,
    ErrorBudgetMonitor,
    LaunchChecklistError,
    LaunchChecklistManager,
    LaunchGatePolicy,
    OperationalHealthMetric,
    OperationsHealthDashboardBuilder,
    ReleasePipelineManager,
    RecoveryStepObservation,
    SLOObservation,
    build_default_core_slo_catalog,
    build_default_disaster_recovery_runbook,
    build_default_launch_gate_policy,
    validate_launch_gate_policy,
)
from runtime.security.operator_drill_scripts import OperatorReadinessReport


class LaunchChecklistManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_core_slo_catalog()
        self.error_budget_report = self._build_error_budget_report()
        self.health_dashboard = self._build_health_dashboard()
        self.disaster_recovery_result = self._build_disaster_recovery_result()
        self.release_pipeline = self._build_release_pipeline(status="promoted")
        self.operator_readiness = self._build_operator_readiness(score=1.0)

    def test_build_checklist_returns_go_when_all_gates_pass(self) -> None:
        checklist = LaunchChecklistManager().build_checklist(
            error_budget_report=self.error_budget_report,
            health_dashboard=self.health_dashboard,
            disaster_recovery_result=self.disaster_recovery_result,
            release_pipeline=self.release_pipeline,
            operator_readiness_report=self.operator_readiness,
        )

        self.assertEqual(checklist.decision, "go")
        self.assertEqual(checklist.fail_count, 0)
        self.assertEqual(checklist.warn_count, 0)

    def test_error_budget_breach_blocks_launch(self) -> None:
        breached_report = self._build_error_budget_report(force_breach=True)

        checklist = LaunchChecklistManager().build_checklist(
            error_budget_report=breached_report,
            health_dashboard=self.health_dashboard,
            disaster_recovery_result=self.disaster_recovery_result,
            release_pipeline=self.release_pipeline,
            operator_readiness_report=self.operator_readiness,
        )

        self.assertEqual(checklist.decision, "block")
        self.assertGreaterEqual(checklist.fail_count, 1)
        gate = next(item for item in checklist.gates if item.gate_id == "error-budget")
        self.assertEqual(gate.status, "fail")

    def test_warning_gate_results_in_hold_when_policy_requires_zero_warnings(self) -> None:
        warning_dashboard = self._build_health_dashboard(score_modifier=0.95)

        checklist = LaunchChecklistManager().build_checklist(
            error_budget_report=self.error_budget_report,
            health_dashboard=warning_dashboard,
            disaster_recovery_result=self.disaster_recovery_result,
            release_pipeline=self.release_pipeline,
            operator_readiness_report=self.operator_readiness,
        )

        self.assertEqual(checklist.decision, "hold")
        self.assertEqual(checklist.fail_count, 0)
        self.assertGreaterEqual(checklist.warn_count, 1)

    def test_release_pipeline_failure_blocks_launch(self) -> None:
        failed_pipeline = self._build_release_pipeline(status="canary_failed")

        checklist = LaunchChecklistManager().build_checklist(
            error_budget_report=self.error_budget_report,
            health_dashboard=self.health_dashboard,
            disaster_recovery_result=self.disaster_recovery_result,
            release_pipeline=failed_pipeline,
            operator_readiness_report=self.operator_readiness,
        )

        self.assertEqual(checklist.decision, "block")
        gate = next(item for item in checklist.gates if item.gate_id == "release-pipeline")
        self.assertEqual(gate.status, "fail")

    def test_manifest_is_deterministic(self) -> None:
        checklist = LaunchChecklistManager().build_checklist(
            error_budget_report=self.error_budget_report,
            health_dashboard=self.health_dashboard,
            disaster_recovery_result=self.disaster_recovery_result,
            release_pipeline=self.release_pipeline,
            operator_readiness_report=self.operator_readiness,
        )

        first = checklist.to_manifest()
        second = checklist.to_manifest()
        self.assertEqual(first, second)

    def test_validate_policy_rejects_invalid_threshold_order(self) -> None:
        invalid = replace(
            build_default_launch_gate_policy(),
            min_dashboard_score=0.98,
            target_dashboard_score=0.95,
        )

        with self.assertRaises(LaunchChecklistError):
            validate_launch_gate_policy(invalid)

    def test_go_allows_warning_when_policy_permits_warning_budget(self) -> None:
        permissive_policy = replace(build_default_launch_gate_policy(), max_warning_gates_for_go=1)
        manager = LaunchChecklistManager(policy=permissive_policy)
        warning_dashboard = self._build_health_dashboard(score_modifier=0.95)

        checklist = manager.build_checklist(
            error_budget_report=self.error_budget_report,
            health_dashboard=warning_dashboard,
            disaster_recovery_result=self.disaster_recovery_result,
            release_pipeline=self.release_pipeline,
            operator_readiness_report=self.operator_readiness,
        )

        self.assertEqual(checklist.decision, "go")
        self.assertEqual(checklist.warn_count, 1)

    def _build_error_budget_report(self, *, force_breach: bool = False):
        observations = []
        for slo in self.catalog.slos:
            compliant_events = 1000
            if force_breach and slo.slo_id == "security_guardrail_enforcement":
                compliant_events = 990

            observations.append(
                SLOObservation(
                    slo_id=slo.slo_id,
                    total_events=1000,
                    compliant_events=compliant_events,
                    elapsed_days=slo.window_days,
                    metadata={"source": "test"},
                )
            )

        return ErrorBudgetMonitor().evaluate_catalog(self.catalog, observations)

    def _build_health_dashboard(self, *, score_modifier: float = 1.0):
        runtime_value = round(0.99 * score_modifier, 6)
        autonomy_value = round(0.985 * score_modifier, 6)
        security_value = round(0.995 * score_modifier, 6)

        return OperationsHealthDashboardBuilder().build_dashboard(
            {
                "runtime": [
                    OperationalHealthMetric(
                        metric_id="runtime_success_ratio",
                        value=runtime_value,
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
                        metric_id="autonomy_cycle_success_ratio",
                        value=autonomy_value,
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
                        metric_id="security_guardrail_ratio",
                        value=security_value,
                        target=0.995,
                        direction="higher_is_better",
                        warning_floor=0.97,
                        critical_floor=0.92,
                        weight=1.0,
                        metadata={},
                    )
                ],
            },
            window_id="launch-gate-window",
            metadata={"source": "test"},
        )

    def _build_disaster_recovery_result(self):
        runbook = build_default_disaster_recovery_runbook()
        observations = []
        for target in runbook.recovery_windows:
            observations.append(
                RecoveryStepObservation(
                    step_id=target.step_id,
                    actual_duration_minutes=max(0.1, target.target_rto_minutes - 1.0),
                    observed_data_loss_minutes=max(0.0, target.target_rpo_minutes - 0.5),
                    status="completed",
                    details=None,
                    metadata={"source": "test"},
                )
            )

        return DisasterRecoveryRunbookManager().evaluate_drill(
            runbook,
            observations,
            strict=True,
        )

    def _build_release_pipeline(self, *, status: str):
        manager = ReleasePipelineManager()
        pipeline = manager.create_pipeline(
            release_id="release-2026-04-22",
            previous_release_id="release-2026-04-21",
            canary_percentage=10,
        )
        pipeline = manager.evaluate_canary(
            pipeline.pipeline_id,
            CanaryObservation(
                request_count=1500,
                error_count=12 if status == "promoted" else 120,
                p95_latency_ms=180.0 if status == "promoted" else 400.0,
                metadata={"source": "test"},
            ),
        )

        if status == "canary_failed":
            return pipeline
        if status == "rolled_back":
            return manager.execute_rollback(
                pipeline_id=pipeline.pipeline_id,
                rollback_token=pipeline.rollback_token or "",
                reason="forced rollback for test",
                actor_role="oncall_engineer",
            )

        return pipeline

    @staticmethod
    def _build_operator_readiness(*, score: float) -> OperatorReadinessReport:
        return OperatorReadinessReport(
            started_at="2026-04-15T00:00:00Z",
            finished_at="2026-04-15T00:10:00Z",
            total_drills=3,
            passed=3 if score >= 0.95 else 2,
            degraded=0 if score >= 0.95 else 1,
            failed=0,
            readiness_score=score,
            summary="operator readiness simulated",
            drills=(),
        )


class LaunchPolicyValidationTests(unittest.TestCase):
    def test_default_policy_validates(self) -> None:
        policy = build_default_launch_gate_policy()
        validate_launch_gate_policy(policy)
        self.assertIsInstance(policy, LaunchGatePolicy)


if __name__ == "__main__":
    unittest.main()
