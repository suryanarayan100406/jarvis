"""Final directive audit and compliance report publication workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from statistics import mean
from typing import Any, Literal

from .compliance_correction import ComplianceCorrectionPlan
from .compliance_dashboard import ComplianceDashboard
from .ethical_refusal import EthicalRefusalDecision
from .persona_compliance import PersonaComplianceBatchReport

AuditCheckStatus = Literal["pass", "warn", "fail"]
DirectiveAuditStatus = Literal["pass", "hold", "fail"]


@dataclass(frozen=True)
class DirectiveAuditCheck:
    check_id: str
    title: str
    status: AuditCheckStatus
    score: float
    detail: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DirectiveAuditReport:
    audit_id: str
    generated_at: str
    status: DirectiveAuditStatus
    readiness_score: float
    check_count: int
    failed_check_count: int
    warning_check_count: int
    required_actions: tuple[str, ...]
    deterministic_digest: str
    checks: tuple[DirectiveAuditCheck, ...]
    markdown: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "generated_at": self.generated_at,
            "status": self.status,
            "readiness_score": self.readiness_score,
            "check_count": self.check_count,
            "failed_check_count": self.failed_check_count,
            "warning_check_count": self.warning_check_count,
            "required_actions": list(self.required_actions),
            "deterministic_digest": self.deterministic_digest,
            "checks": [
                {
                    "check_id": check.check_id,
                    "title": check.title,
                    "status": check.status,
                    "score": check.score,
                    "detail": check.detail,
                    "metadata": dict(check.metadata),
                }
                for check in sorted(self.checks, key=lambda item: item.check_id)
            ],
            "markdown": self.markdown,
            "metadata": dict(self.metadata),
        }


class DirectiveAuditError(ValueError):
    """Raised when directive audit inputs are invalid."""


class DirectiveAuditPublisher:
    """Executes final directive audit and publishes compliance report output."""

    def run_audit(
        self,
        *,
        persona_batch_report: PersonaComplianceBatchReport,
        compliance_dashboard: ComplianceDashboard,
        correction_plan: ComplianceCorrectionPlan,
        ethical_refusal_decisions: list[EthicalRefusalDecision] | tuple[EthicalRefusalDecision, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DirectiveAuditReport:
        if not isinstance(persona_batch_report, PersonaComplianceBatchReport):
            raise TypeError("persona_batch_report must be PersonaComplianceBatchReport")
        if not isinstance(compliance_dashboard, ComplianceDashboard):
            raise TypeError("compliance_dashboard must be ComplianceDashboard")
        if not isinstance(correction_plan, ComplianceCorrectionPlan):
            raise TypeError("correction_plan must be ComplianceCorrectionPlan")

        ethical_decisions = _normalize_ethics_decisions(ethical_refusal_decisions)
        checks = (
            _persona_check(persona_batch_report),
            _drift_check(compliance_dashboard),
            _correction_check(correction_plan),
            _ethical_routing_check(ethical_decisions),
        )

        failed = sum(1 for check in checks if check.status == "fail")
        warnings = sum(1 for check in checks if check.status == "warn")
        readiness_score = round(float(mean(check.score for check in checks)), 12)

        if failed > 0:
            status: DirectiveAuditStatus = "fail"
        elif warnings > 0:
            status = "hold"
        else:
            status = "pass"

        required_actions = _build_required_actions(checks)
        digest = _build_audit_digest(
            status=status,
            readiness_score=readiness_score,
            checks=checks,
            required_actions=required_actions,
        )
        markdown = _render_markdown(
            status=status,
            readiness_score=readiness_score,
            checks=checks,
            required_actions=required_actions,
        )

        return DirectiveAuditReport(
            audit_id=f"directive-audit-{digest[:24]}",
            generated_at=_utc_now_iso(),
            status=status,
            readiness_score=readiness_score,
            check_count=len(checks),
            failed_check_count=failed,
            warning_check_count=warnings,
            required_actions=required_actions,
            deterministic_digest=digest,
            checks=checks,
            markdown=markdown,
            metadata=dict(metadata or {}),
        )


def _persona_check(report: PersonaComplianceBatchReport) -> DirectiveAuditCheck:
    if report.overall_status == "pass":
        status: AuditCheckStatus = "pass"
    elif report.overall_status == "warn":
        status = "warn"
    else:
        status = "fail"

    return DirectiveAuditCheck(
        check_id="persona-compliance",
        title="Persona Compliance Baseline",
        status=status,
        score=report.overall_score,
        detail=(
            f"Persona compliance overall_status={report.overall_status} "
            f"with overall_score={report.overall_score:.4f}."
        ),
        metadata={
            "report_count": len(report.reports),
            "overall_status": report.overall_status,
        },
    )


def _drift_check(dashboard: ComplianceDashboard) -> DirectiveAuditCheck:
    critical_count = sum(1 for alert in dashboard.drift_alerts if alert.severity == "critical")
    warning_count = sum(1 for alert in dashboard.drift_alerts if alert.severity == "warning")

    if critical_count > 0:
        status: AuditCheckStatus = "fail"
        score = 0.3
    elif warning_count > 0:
        status = "warn"
        score = 0.7
    else:
        status = "pass"
        score = 1.0

    return DirectiveAuditCheck(
        check_id="compliance-drift",
        title="Compliance Drift Posture",
        status=status,
        score=score,
        detail=(
            f"Drift alerts: critical={critical_count}, warning={warning_count}, "
            f"overall_status={dashboard.overall_status}."
        ),
        metadata={
            "critical_alert_count": critical_count,
            "warning_alert_count": warning_count,
            "dashboard_status": dashboard.overall_status,
        },
    )


def _correction_check(plan: ComplianceCorrectionPlan) -> DirectiveAuditCheck:
    unresolved = plan.open_count + plan.in_progress_count

    if unresolved == 0:
        status: AuditCheckStatus = "pass"
        score = 1.0
    elif unresolved <= 2:
        status = "warn"
        score = 0.65
    else:
        status = "fail"
        score = 0.3

    return DirectiveAuditCheck(
        check_id="correction-closure",
        title="Correction Workflow Closure",
        status=status,
        score=score,
        detail=(
            f"Correction plan {plan.plan_id} unresolved tasks={unresolved} "
            f"(open={plan.open_count}, in_progress={plan.in_progress_count})."
        ),
        metadata={
            "plan_id": plan.plan_id,
            "plan_status": plan.status,
            "task_count": plan.task_count,
            "unresolved_count": unresolved,
        },
    )


def _ethical_routing_check(
    decisions: tuple[EthicalRefusalDecision, ...],
) -> DirectiveAuditCheck:
    refused = [decision for decision in decisions if decision.status == "refuse"]
    failed_alt_checks = sum(
        1
        for decision in refused
        for check in decision.alternative_checks
        if check.status == "fail"
    )

    if not refused:
        status: AuditCheckStatus = "pass"
        score = 1.0
        detail = "No refused decisions in audit window; ethical-routing failures not observed."
    elif failed_alt_checks == 0:
        status = "pass"
        score = 1.0
        detail = f"Reviewed {len(refused)} refused decisions with no failed alternative-path checks."
    elif failed_alt_checks == 1:
        status = "warn"
        score = 0.7
        detail = (
            f"Reviewed {len(refused)} refused decisions; one failed alternative-path check requires correction."
        )
    else:
        status = "fail"
        score = 0.3
        detail = (
            f"Reviewed {len(refused)} refused decisions; {failed_alt_checks} failed alternative-path checks detected."
        )

    return DirectiveAuditCheck(
        check_id="ethical-routing",
        title="Ethical Refusal Routing Integrity",
        status=status,
        score=score,
        detail=detail,
        metadata={
            "refused_count": len(refused),
            "failed_alternative_checks": failed_alt_checks,
        },
    )


def _build_required_actions(checks: tuple[DirectiveAuditCheck, ...]) -> tuple[str, ...]:
    actions: list[str] = []
    for check in checks:
        if check.status == "pass":
            continue
        if check.check_id == "persona-compliance":
            actions.append("Resolve failing persona compliance checks and rerun profile compliance evaluation.")
        elif check.check_id == "compliance-drift":
            actions.append("Mitigate drift alerts and restore dashboard component trends above warning thresholds.")
        elif check.check_id == "correction-closure":
            actions.append("Close or waive all unresolved correction tasks with documented reviewer rationale.")
        elif check.check_id == "ethical-routing":
            actions.append("Fix ethical refusal alternative-path failures and revalidate refusal routing checks.")
        else:
            actions.append("Address non-passing directive audit checks before release sign-off.")

    deduped = sorted(set(actions))
    return tuple(deduped)


def _build_audit_digest(
    *,
    status: DirectiveAuditStatus,
    readiness_score: float,
    checks: tuple[DirectiveAuditCheck, ...],
    required_actions: tuple[str, ...],
) -> str:
    canonical = json.dumps(
        {
            "status": status,
            "readiness_score": readiness_score,
            "checks": [
                {
                    "check_id": check.check_id,
                    "status": check.status,
                    "score": check.score,
                    "detail": check.detail,
                }
                for check in sorted(checks, key=lambda item: item.check_id)
            ],
            "required_actions": list(required_actions),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _render_markdown(
    *,
    status: DirectiveAuditStatus,
    readiness_score: float,
    checks: tuple[DirectiveAuditCheck, ...],
    required_actions: tuple[str, ...],
) -> str:
    lines = [
        "# Directive Compliance Audit Report",
        "",
        f"Audit status: {status}",
        f"Readiness score: {readiness_score:.4f}",
        "",
        "## Check Results",
    ]

    for check in sorted(checks, key=lambda item: item.check_id):
        lines.append(
            (
                f"- {check.check_id}: status={check.status}, score={check.score:.4f}"
                f" | {check.detail}"
            )
        )

    lines.append("")
    lines.append("## Required Actions")
    if not required_actions:
        lines.append("- none")
    else:
        for action in required_actions:
            lines.append(f"- {action}")

    return "\n".join(lines)


def _normalize_ethics_decisions(
    decisions: list[EthicalRefusalDecision] | tuple[EthicalRefusalDecision, ...] | None,
) -> tuple[EthicalRefusalDecision, ...]:
    if decisions is None:
        return ()
    if not isinstance(decisions, (list, tuple)):
        raise TypeError("ethical_refusal_decisions must be a list or tuple of EthicalRefusalDecision")

    normalized: list[EthicalRefusalDecision] = []
    for decision in decisions:
        if not isinstance(decision, EthicalRefusalDecision):
            raise TypeError("ethical_refusal_decisions must contain EthicalRefusalDecision")
        normalized.append(decision)
    return tuple(sorted(normalized, key=lambda item: item.decision_id))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "AuditCheckStatus",
    "DirectiveAuditStatus",
    "DirectiveAuditCheck",
    "DirectiveAuditReport",
    "DirectiveAuditError",
    "DirectiveAuditPublisher",
]
