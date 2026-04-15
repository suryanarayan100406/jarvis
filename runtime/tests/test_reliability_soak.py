"""Reliability soak tests for P11-T9 sustained launch-readiness operation."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.orchestration import (
    BackupStrategyManager,
    CanaryObservation,
    DisasterRecoveryRunbookManager,
    ErrorBudgetMonitor,
    LaunchChecklistManager,
    OperationalHealthMetric,
    OperationsHealthDashboardBuilder,
    RecoveryStepObservation,
    ReleasePipelineManager,
    RestoreWorkflowEngine,
    SLOObservation,
    build_default_core_slo_catalog,
    build_default_disaster_recovery_runbook,
    build_default_launch_gate_policy,
)
from runtime.security.operator_drill_scripts import OperatorReadinessReport


class ReliabilitySoakTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_core_slo_catalog()
        self.error_budget_monitor = ErrorBudgetMonitor()
        self.health_builder = OperationsHealthDashboardBuilder()
        self.backup_manager = BackupStrategyManager()
        self.restore_engine = RestoreWorkflowEngine()
        self.dr_runbook = build_default_disaster_recovery_runbook()
        self.dr_manager = DisasterRecoveryRunbookManager()
        self.launch_manager = LaunchChecklistManager()

    def test_nominal_sustained_operation_keeps_launch_gate_green(self) -> None:
        decisions: list[str] = []

        for cycle in range(0, 96):
            payloads = self._payloads_for_cycle(cycle)
            backup_record = self.backup_manager.run_backup(payloads)
            restore_result = self.restore_engine.restore_from_backup(backup_record, payloads)
            self.assertEqual(restore_result.status, "completed")

            checklist = self.launch_manager.build_checklist(
                error_budget_report=self._build_error_budget_report(),
                health_dashboard=self._build_health_dashboard(score_modifier=1.0),
                disaster_recovery_result=self._build_disaster_recovery_result(),
                release_pipeline=self._build_release_pipeline(
                    release_id=f"release-2026-05-{cycle + 1:02d}",
                    previous_release_id=f"release-2026-05-{cycle:02d}",
                    canary_fail=False,
                ),
                operator_readiness_report=self._build_operator_readiness(
                    readiness_score=1.0,
                    degraded=0,
                    failed=0,
                ),
            )
            decisions.append(checklist.decision)

        self.assertEqual(len(decisions), 96)
        self.assertTrue(all(decision == "go" for decision in decisions))

    def test_periodic_health_warning_holds_launch_without_blocking(self) -> None:
        decisions: list[str] = []

        for cycle in range(0, 24):
            warning_cycle = cycle % 6 == 0
            checklist = self.launch_manager.build_checklist(
                error_budget_report=self._build_error_budget_report(),
                health_dashboard=self._build_health_dashboard(
                    score_modifier=0.95 if warning_cycle else 1.0
                ),
                disaster_recovery_result=self._build_disaster_recovery_result(),
                release_pipeline=self._build_release_pipeline(
                    release_id=f"release-2026-06-{cycle + 1:02d}",
                    previous_release_id=f"release-2026-06-{cycle:02d}",
                    canary_fail=False,
                ),
                operator_readiness_report=self._build_operator_readiness(
                    readiness_score=1.0,
                    degraded=0,
                    failed=0,
                ),
            )
            decisions.append(checklist.decision)

        self.assertGreater(decisions.count("hold"), 0)
        self.assertEqual(decisions.count("block"), 0)

    def test_canary_failure_and_rollback_blocks_launch_gate(self) -> None:
        release_pipeline = self._build_release_pipeline(
            release_id="release-2026-07-01",
            previous_release_id="release-2026-06-30",
            canary_fail=True,
            execute_rollback=True,
        )

        checklist = self.launch_manager.build_checklist(
            error_budget_report=self._build_error_budget_report(),
            health_dashboard=self._build_health_dashboard(score_modifier=1.0),
            disaster_recovery_result=self._build_disaster_recovery_result(),
            release_pipeline=release_pipeline,
            operator_readiness_report=self._build_operator_readiness(
                readiness_score=1.0,
                degraded=0,
                failed=0,
            ),
        )

        self.assertEqual(checklist.decision, "block")
        release_gate = next(gate for gate in checklist.gates if gate.gate_id == "release-pipeline")
        self.assertEqual(release_gate.status, "fail")

    def _build_error_budget_report(self):
        observations = [
            SLOObservation(
                slo_id=slo.slo_id,
                total_events=1000,
                compliant_events=1000,
                elapsed_days=slo.window_days,
                metadata={"source": "soak"},
            )
            for slo in self.catalog.slos
        ]
        return self.error_budget_monitor.evaluate_catalog(self.catalog, observations)

    def _build_health_dashboard(self, *, score_modifier: float):
        return self.health_builder.build_dashboard(
            {
                "runtime": [
                    OperationalHealthMetric(
                        metric_id="runtime_success_ratio",
                        value=round(0.99 * score_modifier, 6),
                        target=0.99,
                        direction="higher_is_better",
                        warning_floor=0.96,
                        critical_floor=0.9,
                        weight=1.0,
                        metadata={"source": "soak"},
                    )
                ],
                "autonomy": [
                    OperationalHealthMetric(
                        metric_id="autonomy_cycle_success_ratio",
                        value=round(0.985 * score_modifier, 6),
                        target=0.985,
                        direction="higher_is_better",
                        warning_floor=0.96,
                        critical_floor=0.9,
                        weight=1.0,
                        metadata={"source": "soak"},
                    )
                ],
                "security": [
                    OperationalHealthMetric(
                        metric_id="security_guardrail_ratio",
                        value=round(0.995 * score_modifier, 6),
                        target=0.995,
                        direction="higher_is_better",
                        warning_floor=0.97,
                        critical_floor=0.92,
                        weight=1.0,
                        metadata={"source": "soak"},
                    )
                ],
            },
            window_id="soak-window",
            metadata={"source": "soak"},
        )

    def _build_disaster_recovery_result(self):
        observations = [
            RecoveryStepObservation(
                step_id=target.step_id,
                actual_duration_minutes=max(0.1, target.target_rto_minutes - 1.0),
                observed_data_loss_minutes=max(0.0, target.target_rpo_minutes - 0.5),
                status="completed",
                details=None,
                metadata={"source": "soak"},
            )
            for target in self.dr_runbook.recovery_windows
        ]
        return self.dr_manager.evaluate_drill(
            self.dr_runbook,
            observations,
            strict=True,
        )

    def _build_release_pipeline(
        self,
        *,
        release_id: str,
        previous_release_id: str,
        canary_fail: bool,
        execute_rollback: bool = False,
    ):
        manager = ReleasePipelineManager()
        pipeline = manager.create_pipeline(
            release_id=release_id,
            previous_release_id=previous_release_id,
            canary_percentage=10,
        )

        pipeline = manager.evaluate_canary(
            pipeline.pipeline_id,
            CanaryObservation(
                request_count=1500,
                error_count=120 if canary_fail else 12,
                p95_latency_ms=420.0 if canary_fail else 180.0,
                metadata={"source": "soak"},
            ),
        )

        if execute_rollback:
            pipeline = manager.execute_rollback(
                pipeline_id=pipeline.pipeline_id,
                rollback_token=pipeline.rollback_token or "",
                reason="soak rollback",
                actor_role="oncall_engineer",
            )

        return pipeline

    @staticmethod
    def _build_operator_readiness(
        *,
        readiness_score: float,
        degraded: int,
        failed: int,
    ) -> OperatorReadinessReport:
        return OperatorReadinessReport(
            started_at="2026-05-01T00:00:00Z",
            finished_at="2026-05-01T00:05:00Z",
            total_drills=3,
            passed=max(0, 3 - degraded - failed),
            degraded=degraded,
            failed=failed,
            readiness_score=readiness_score,
            summary="soak readiness signal",
            drills=(),
        )

    @staticmethod
    def _payloads_for_cycle(cycle: int) -> dict[str, str]:
        return {
            "state": f"phase=11\ncycle={cycle}",
            "memory": f"memory checkpoint cycle={cycle}",
            "configuration": "strict_mode=true\npolicy=enforced",
        }


class LaunchPolicyBehaviorTests(unittest.TestCase):
    def test_warning_budget_changes_soak_decision_outcome(self) -> None:
        policy = build_default_launch_gate_policy()
        strict_manager = LaunchChecklistManager(policy=policy)
        permissive_manager = LaunchChecklistManager(
            policy=replace(policy, max_warning_gates_for_go=1)
        )

        catalog = build_default_core_slo_catalog()
        report = ErrorBudgetMonitor().evaluate_catalog(
            catalog,
            [
                SLOObservation(
                    slo_id=slo.slo_id,
                    total_events=1000,
                    compliant_events=1000,
                    elapsed_days=slo.window_days,
                    metadata={},
                )
                for slo in catalog.slos
            ],
        )

        dashboard = OperationsHealthDashboardBuilder().build_dashboard(
            {
                "runtime": [
                    OperationalHealthMetric(
                        metric_id="runtime",
                        value=0.95,
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
                        value=0.985,
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
                        value=0.995,
                        target=0.995,
                        direction="higher_is_better",
                        warning_floor=0.97,
                        critical_floor=0.92,
                        weight=1.0,
                        metadata={},
                    )
                ],
            },
            window_id="warning-budget-window",
            metadata={},
        )

        runbook = build_default_disaster_recovery_runbook()
        dr_result = DisasterRecoveryRunbookManager().evaluate_drill(
            runbook,
            [
                RecoveryStepObservation(
                    step_id=target.step_id,
                    actual_duration_minutes=max(0.1, target.target_rto_minutes - 1.0),
                    observed_data_loss_minutes=max(0.0, target.target_rpo_minutes - 0.5),
                    status="completed",
                    details=None,
                    metadata={},
                )
                for target in runbook.recovery_windows
            ],
            strict=True,
        )

        pipeline_manager = ReleasePipelineManager()
        pipeline = pipeline_manager.create_pipeline(
            release_id="release-2026-08-01",
            previous_release_id="release-2026-07-31",
            canary_percentage=10,
        )
        pipeline = pipeline_manager.evaluate_canary(
            pipeline.pipeline_id,
            CanaryObservation(
                request_count=1500,
                error_count=10,
                p95_latency_ms=180.0,
                metadata={},
            ),
        )

        readiness = OperatorReadinessReport(
            started_at="2026-08-01T00:00:00Z",
            finished_at="2026-08-01T00:03:00Z",
            total_drills=3,
            passed=3,
            degraded=0,
            failed=0,
            readiness_score=1.0,
            summary="ready",
            drills=(),
        )

        strict = strict_manager.build_checklist(
            error_budget_report=report,
            health_dashboard=dashboard,
            disaster_recovery_result=dr_result,
            release_pipeline=pipeline,
            operator_readiness_report=readiness,
        )
        permissive = permissive_manager.build_checklist(
            error_budget_report=report,
            health_dashboard=dashboard,
            disaster_recovery_result=dr_result,
            release_pipeline=pipeline,
            operator_readiness_report=readiness,
        )

        self.assertEqual(strict.decision, "hold")
        self.assertEqual(permissive.decision, "go")


if __name__ == "__main__":
    unittest.main()
