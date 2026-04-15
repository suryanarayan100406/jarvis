"""Launch checklist automation and gate validation for release readiness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from runtime.orchestration.disaster_recovery_runbook import DisasterRecoveryDrillResult
from runtime.orchestration.operations_health_dashboard import OperationsHealthDashboard
from runtime.orchestration.release_pipeline import ReleasePipelineRecord
from runtime.orchestration.slo_error_budget import ErrorBudgetReport
from runtime.security.operator_drill_scripts import OperatorReadinessReport

LaunchGateStatus = Literal["pass", "warn", "fail"]
LaunchDecision = Literal["go", "hold", "block"]


@dataclass(frozen=True)
class LaunchGatePolicy:
    policy_id: str
    policy_version: str
    min_dashboard_score: float
    target_dashboard_score: float
    min_operator_readiness_score: float
    target_operator_readiness_score: float
    max_warning_gates_for_go: int
    max_disaster_recovery_breaches: int
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "min_dashboard_score": self.min_dashboard_score,
            "target_dashboard_score": self.target_dashboard_score,
            "min_operator_readiness_score": self.min_operator_readiness_score,
            "target_operator_readiness_score": self.target_operator_readiness_score,
            "max_warning_gates_for_go": self.max_warning_gates_for_go,
            "max_disaster_recovery_breaches": self.max_disaster_recovery_breaches,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class LaunchGateResult:
    gate_id: str
    title: str
    status: LaunchGateStatus
    detail: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class LaunchReadinessChecklist:
    checklist_id: str
    created_at: str
    policy_id: str
    policy_version: str
    decision: LaunchDecision
    pass_count: int
    warn_count: int
    fail_count: int
    deterministic_digest: str
    summary: str
    gates: tuple[LaunchGateResult, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "checklist_id": self.checklist_id,
            "created_at": self.created_at,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "decision": self.decision,
            "pass_count": self.pass_count,
            "warn_count": self.warn_count,
            "fail_count": self.fail_count,
            "deterministic_digest": self.deterministic_digest,
            "summary": self.summary,
            "gates": [
                {
                    "gate_id": gate.gate_id,
                    "title": gate.title,
                    "status": gate.status,
                    "detail": gate.detail,
                    "evidence": dict(gate.evidence),
                }
                for gate in self.gates
            ],
            "metadata": dict(self.metadata),
        }


class LaunchChecklistError(ValueError):
    """Raised when launch checklist input or gate policy is invalid."""


class LaunchChecklistManager:
    """Builds deterministic launch checklists from reliability and release signals."""

    def __init__(self, policy: LaunchGatePolicy | None = None) -> None:
        if policy is None:
            policy = build_default_launch_gate_policy()
        validate_launch_gate_policy(policy)
        self.policy = policy

    def build_checklist(
        self,
        *,
        error_budget_report: ErrorBudgetReport,
        health_dashboard: OperationsHealthDashboard,
        disaster_recovery_result: DisasterRecoveryDrillResult,
        release_pipeline: ReleasePipelineRecord,
        operator_readiness_report: OperatorReadinessReport,
        metadata: dict[str, Any] | None = None,
    ) -> LaunchReadinessChecklist:
        if not isinstance(error_budget_report, ErrorBudgetReport):
            raise TypeError("error_budget_report must be ErrorBudgetReport")
        if not isinstance(health_dashboard, OperationsHealthDashboard):
            raise TypeError("health_dashboard must be OperationsHealthDashboard")
        if not isinstance(disaster_recovery_result, DisasterRecoveryDrillResult):
            raise TypeError("disaster_recovery_result must be DisasterRecoveryDrillResult")
        if not isinstance(release_pipeline, ReleasePipelineRecord):
            raise TypeError("release_pipeline must be ReleasePipelineRecord")
        if not isinstance(operator_readiness_report, OperatorReadinessReport):
            raise TypeError("operator_readiness_report must be OperatorReadinessReport")

        gates = (
            self._evaluate_error_budget_gate(error_budget_report),
            self._evaluate_health_dashboard_gate(health_dashboard),
            self._evaluate_disaster_recovery_gate(disaster_recovery_result),
            self._evaluate_release_pipeline_gate(release_pipeline),
            self._evaluate_operator_readiness_gate(operator_readiness_report),
        )

        pass_count = sum(1 for gate in gates if gate.status == "pass")
        warn_count = sum(1 for gate in gates if gate.status == "warn")
        fail_count = sum(1 for gate in gates if gate.status == "fail")

        if fail_count > 0:
            decision: LaunchDecision = "block"
        elif warn_count > self.policy.max_warning_gates_for_go:
            decision = "hold"
        else:
            decision = "go"

        summary = (
            f"Launch decision={decision}; gates pass={pass_count}, warn={warn_count}, fail={fail_count}."
        )

        deterministic_digest = _build_checklist_digest(
            policy=self.policy,
            decision=decision,
            gates=gates,
        )

        return LaunchReadinessChecklist(
            checklist_id=f"launch-checklist-{deterministic_digest[:20]}",
            created_at=_utc_now_iso(),
            policy_id=self.policy.policy_id,
            policy_version=self.policy.policy_version,
            decision=decision,
            pass_count=pass_count,
            warn_count=warn_count,
            fail_count=fail_count,
            deterministic_digest=deterministic_digest,
            summary=summary,
            gates=gates,
            metadata=dict(metadata or {}),
        )

    def _evaluate_error_budget_gate(self, report: ErrorBudgetReport) -> LaunchGateResult:
        evidence = {
            "report_id": report.report_id,
            "evaluation_count": report.evaluation_count,
            "warning_count": report.warning_count,
            "critical_count": report.critical_count,
            "breached_count": report.breached_count,
        }

        if report.breached_count > 0 or report.critical_count > 0:
            return LaunchGateResult(
                gate_id="error-budget",
                title="SLO Error Budget",
                status="fail",
                detail="Error budget contains critical or breached SLO evaluations.",
                evidence=evidence,
            )

        if report.warning_count > 0:
            return LaunchGateResult(
                gate_id="error-budget",
                title="SLO Error Budget",
                status="warn",
                detail="Error budget includes warning-level SLO burn-rate signals.",
                evidence=evidence,
            )

        return LaunchGateResult(
            gate_id="error-budget",
            title="SLO Error Budget",
            status="pass",
            detail="All SLO error-budget evaluations are healthy.",
            evidence=evidence,
        )

    def _evaluate_health_dashboard_gate(self, dashboard: OperationsHealthDashboard) -> LaunchGateResult:
        evidence = {
            "dashboard_id": dashboard.dashboard_id,
            "overall_status": dashboard.overall_status,
            "overall_score": dashboard.overall_score,
            "window_id": dashboard.window_id,
        }

        if dashboard.overall_status == "critical" or dashboard.overall_score < self.policy.min_dashboard_score:
            return LaunchGateResult(
                gate_id="operations-health",
                title="Operations Health",
                status="fail",
                detail="Operations dashboard is below minimum launch score or in critical state.",
                evidence=evidence,
            )

        if dashboard.overall_status == "warning" or dashboard.overall_score < self.policy.target_dashboard_score:
            return LaunchGateResult(
                gate_id="operations-health",
                title="Operations Health",
                status="warn",
                detail="Operations dashboard is above minimum but below target readiness posture.",
                evidence=evidence,
            )

        return LaunchGateResult(
            gate_id="operations-health",
            title="Operations Health",
            status="pass",
            detail="Operations dashboard meets launch readiness targets.",
            evidence=evidence,
        )

    def _evaluate_disaster_recovery_gate(self, result: DisasterRecoveryDrillResult) -> LaunchGateResult:
        evidence = {
            "drill_id": result.drill_id,
            "status": result.status,
            "windows_breached_count": result.windows_breached_count,
            "failed_steps": result.failed_steps,
            "skipped_steps": result.skipped_steps,
        }

        if result.status == "failed" or result.windows_breached_count > self.policy.max_disaster_recovery_breaches:
            return LaunchGateResult(
                gate_id="disaster-recovery",
                title="Disaster Recovery",
                status="fail",
                detail="Disaster recovery drill failed required windows or exceeded breach allowance.",
                evidence=evidence,
            )

        if result.status == "degraded" or result.windows_breached_count > 0:
            return LaunchGateResult(
                gate_id="disaster-recovery",
                title="Disaster Recovery",
                status="warn",
                detail="Disaster recovery drill completed with non-blocking degradation.",
                evidence=evidence,
            )

        return LaunchGateResult(
            gate_id="disaster-recovery",
            title="Disaster Recovery",
            status="pass",
            detail="Disaster recovery drill met required recovery windows.",
            evidence=evidence,
        )

    def _evaluate_release_pipeline_gate(self, pipeline: ReleasePipelineRecord) -> LaunchGateResult:
        evidence = {
            "pipeline_id": pipeline.pipeline_id,
            "status": pipeline.status,
            "canary_percentage": pipeline.canary_percentage,
            "rollback_state": pipeline.rollback_state,
        }

        if pipeline.status in {"canary_failed", "rolled_back"}:
            return LaunchGateResult(
                gate_id="release-pipeline",
                title="Release Pipeline",
                status="fail",
                detail="Release pipeline indicates canary failure or rollback execution.",
                evidence=evidence,
            )

        if pipeline.status == "pending_canary":
            return LaunchGateResult(
                gate_id="release-pipeline",
                title="Release Pipeline",
                status="warn",
                detail="Release pipeline has not completed canary validation yet.",
                evidence=evidence,
            )

        return LaunchGateResult(
            gate_id="release-pipeline",
            title="Release Pipeline",
            status="pass",
            detail="Release pipeline is promoted with rollback support available.",
            evidence=evidence,
        )

    def _evaluate_operator_readiness_gate(self, report: OperatorReadinessReport) -> LaunchGateResult:
        evidence = {
            "total_drills": report.total_drills,
            "passed": report.passed,
            "degraded": report.degraded,
            "failed": report.failed,
            "readiness_score": report.readiness_score,
        }

        if report.readiness_score < self.policy.min_operator_readiness_score or report.failed > 0:
            return LaunchGateResult(
                gate_id="operator-readiness",
                title="Operator Readiness",
                status="fail",
                detail="Operator readiness score is below minimum launch threshold or includes failed drills.",
                evidence=evidence,
            )

        if (
            report.readiness_score < self.policy.target_operator_readiness_score
            or report.degraded > 0
        ):
            return LaunchGateResult(
                gate_id="operator-readiness",
                title="Operator Readiness",
                status="warn",
                detail="Operator readiness is acceptable but below launch target confidence.",
                evidence=evidence,
            )

        return LaunchGateResult(
            gate_id="operator-readiness",
            title="Operator Readiness",
            status="pass",
            detail="Operator readiness drills meet launch targets.",
            evidence=evidence,
        )


def build_default_launch_gate_policy() -> LaunchGatePolicy:
    policy = LaunchGatePolicy(
        policy_id="friday-launch-gate-policy",
        policy_version="1.0.0",
        min_dashboard_score=0.9,
        target_dashboard_score=0.97,
        min_operator_readiness_score=0.85,
        target_operator_readiness_score=0.95,
        max_warning_gates_for_go=0,
        max_disaster_recovery_breaches=0,
        metadata={
            "program": "production_reliability",
            "phase": "P11-T8",
            "notes": "Launch checklist gate policy for automated go or hold or block decisions.",
        },
    )
    validate_launch_gate_policy(policy)
    return policy


def validate_launch_gate_policy(policy: LaunchGatePolicy) -> None:
    if not isinstance(policy, LaunchGatePolicy):
        raise TypeError("policy must be LaunchGatePolicy")

    _normalize_required(policy.policy_id, "policy_id")
    _normalize_required(policy.policy_version, "policy_version")

    for field_name, value in (
        ("min_dashboard_score", policy.min_dashboard_score),
        ("target_dashboard_score", policy.target_dashboard_score),
        ("min_operator_readiness_score", policy.min_operator_readiness_score),
        ("target_operator_readiness_score", policy.target_operator_readiness_score),
    ):
        if not isinstance(value, (int, float)):
            raise TypeError(f"{field_name} must be numeric")
        if value < 0 or value > 1:
            raise LaunchChecklistError(f"{field_name} must be between 0 and 1")

    if policy.target_dashboard_score < policy.min_dashboard_score:
        raise LaunchChecklistError("target_dashboard_score must be >= min_dashboard_score")
    if policy.target_operator_readiness_score < policy.min_operator_readiness_score:
        raise LaunchChecklistError(
            "target_operator_readiness_score must be >= min_operator_readiness_score"
        )

    if not isinstance(policy.max_warning_gates_for_go, int):
        raise TypeError("max_warning_gates_for_go must be an integer")
    if policy.max_warning_gates_for_go < 0:
        raise LaunchChecklistError("max_warning_gates_for_go cannot be negative")

    if not isinstance(policy.max_disaster_recovery_breaches, int):
        raise TypeError("max_disaster_recovery_breaches must be an integer")
    if policy.max_disaster_recovery_breaches < 0:
        raise LaunchChecklistError("max_disaster_recovery_breaches cannot be negative")


def _build_checklist_digest(
    *,
    policy: LaunchGatePolicy,
    decision: LaunchDecision,
    gates: tuple[LaunchGateResult, ...],
) -> str:
    canonical = json.dumps(
        {
            "policy": policy.to_manifest(),
            "decision": decision,
            "gates": [
                {
                    "gate_id": gate.gate_id,
                    "status": gate.status,
                    "detail": gate.detail,
                    "evidence": dict(gate.evidence),
                }
                for gate in gates
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise LaunchChecklistError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "LaunchGateStatus",
    "LaunchDecision",
    "LaunchGatePolicy",
    "LaunchGateResult",
    "LaunchReadinessChecklist",
    "LaunchChecklistError",
    "LaunchChecklistManager",
    "build_default_launch_gate_policy",
    "validate_launch_gate_policy",
]
