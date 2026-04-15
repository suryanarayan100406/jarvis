"""Correction workflow for failed compliance checks."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from .compliance_dashboard import ComplianceDashboard
from .ethical_refusal import EthicalRefusalDecision
from .persona_compliance import PersonaComplianceBatchReport

CorrectionTaskPriority = Literal["low", "medium", "high", "critical"]
CorrectionTaskStatus = Literal["open", "in_progress", "resolved", "waived"]
CorrectionPlanStatus = Literal["open", "resolved"]


@dataclass(frozen=True)
class ComplianceCorrectionTask:
    task_id: str
    source_type: str
    source_id: str
    component_id: str
    priority: CorrectionTaskPriority
    status: CorrectionTaskStatus
    reason: str
    recommended_action: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ComplianceCorrectionEvent:
    event_id: str
    scope_id: str
    actor_id: str
    action: str
    previous_status: str | None
    new_status: str | None
    note: str | None
    created_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ComplianceCorrectionPlan:
    plan_id: str
    created_at: str
    status: CorrectionPlanStatus
    task_count: int
    open_count: int
    in_progress_count: int
    resolved_count: int
    waived_count: int
    critical_count: int
    high_count: int
    deterministic_digest: str
    tasks: tuple[ComplianceCorrectionTask, ...]
    events: tuple[ComplianceCorrectionEvent, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "status": self.status,
            "task_count": self.task_count,
            "open_count": self.open_count,
            "in_progress_count": self.in_progress_count,
            "resolved_count": self.resolved_count,
            "waived_count": self.waived_count,
            "critical_count": self.critical_count,
            "high_count": self.high_count,
            "deterministic_digest": self.deterministic_digest,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "source_type": task.source_type,
                    "source_id": task.source_id,
                    "component_id": task.component_id,
                    "priority": task.priority,
                    "status": task.status,
                    "reason": task.reason,
                    "recommended_action": task.recommended_action,
                    "metadata": dict(task.metadata),
                }
                for task in sorted(self.tasks, key=lambda item: item.task_id)
            ],
            "events": [
                {
                    "event_id": event.event_id,
                    "scope_id": event.scope_id,
                    "actor_id": event.actor_id,
                    "action": event.action,
                    "previous_status": event.previous_status,
                    "new_status": event.new_status,
                    "note": event.note,
                    "created_at": event.created_at,
                    "metadata": dict(event.metadata),
                }
                for event in sorted(self.events, key=lambda item: item.event_id)
            ],
            "metadata": dict(self.metadata),
        }


class ComplianceCorrectionError(ValueError):
    """Raised when compliance correction workflow inputs are invalid."""


class ComplianceCorrectionWorkflow:
    """Builds and manages correction plans for failed compliance signals."""

    def __init__(self) -> None:
        self._plans: dict[str, ComplianceCorrectionPlan] = {}

    def build_plan(
        self,
        *,
        persona_batch_report: PersonaComplianceBatchReport,
        compliance_dashboard: ComplianceDashboard,
        ethical_refusal_decisions: list[EthicalRefusalDecision] | tuple[EthicalRefusalDecision, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ComplianceCorrectionPlan:
        if not isinstance(persona_batch_report, PersonaComplianceBatchReport):
            raise TypeError("persona_batch_report must be PersonaComplianceBatchReport")
        if not isinstance(compliance_dashboard, ComplianceDashboard):
            raise TypeError("compliance_dashboard must be ComplianceDashboard")

        normalized_decisions = _normalize_ethics_decisions(ethical_refusal_decisions)
        created_at = _utc_now_iso()
        plan_id = f"compliance-correction-{len(self._plans) + 1:04d}"

        candidate_tasks: list[ComplianceCorrectionTask] = []
        dedupe: set[tuple[str, str, str]] = set()

        for report in persona_batch_report.reports:
            for check in report.checks:
                if check.status == "pass":
                    continue
                dedupe_key = ("persona_compliance_check", f"{report.report_id}:{check.check_id}", report.profile_id)
                if dedupe_key in dedupe:
                    continue
                dedupe.add(dedupe_key)

                priority = "high" if check.status == "fail" else "medium"
                if check.check_id == "sample-coverage" and check.status == "fail":
                    priority = "critical"

                candidate_tasks.append(
                    ComplianceCorrectionTask(
                        task_id="",
                        source_type="persona_compliance_check",
                        source_id=f"{report.report_id}:{check.check_id}",
                        component_id=f"{report.profile_id}.{check.check_id}",
                        priority=priority,
                        status="open",
                        reason=check.detail,
                        recommended_action=_recommended_action_for_check(check.check_id),
                        metadata={
                            "profile_id": report.profile_id,
                            "check_id": check.check_id,
                            "check_status": check.status,
                            "score": check.score,
                        },
                    )
                )

        for alert in compliance_dashboard.drift_alerts:
            dedupe_key = ("drift_alert", alert.alert_id, alert.component_id)
            if dedupe_key in dedupe:
                continue
            dedupe.add(dedupe_key)

            candidate_tasks.append(
                ComplianceCorrectionTask(
                    task_id="",
                    source_type="drift_alert",
                    source_id=alert.alert_id,
                    component_id=alert.component_id,
                    priority="critical" if alert.severity == "critical" else "high",
                    status="open",
                    reason=alert.reason,
                    recommended_action=(
                        "Investigate the declining trend, apply mitigations, and restore component score above drift threshold."
                    ),
                    metadata={
                        "severity": alert.severity,
                        "delta": alert.delta,
                        "threshold": alert.threshold,
                    },
                )
            )

        for decision in normalized_decisions:
            if decision.status != "refuse":
                continue
            for check in decision.alternative_checks:
                if check.status != "fail":
                    continue
                dedupe_key = (
                    "ethical_refusal_check",
                    f"{decision.decision_id}:{check.check_id}",
                    decision.profile_id,
                )
                if dedupe_key in dedupe:
                    continue
                dedupe.add(dedupe_key)

                candidate_tasks.append(
                    ComplianceCorrectionTask(
                        task_id="",
                        source_type="ethical_refusal_check",
                        source_id=f"{decision.decision_id}:{check.check_id}",
                        component_id=f"ethical_refusal.{check.check_id}",
                        priority="high",
                        status="open",
                        reason=check.detail,
                        recommended_action=(
                            "Revise alternative-path generation to guarantee policy-safe fallback guidance."
                        ),
                        metadata={
                            "decision_id": decision.decision_id,
                            "check_id": check.check_id,
                            "profile_id": decision.profile_id,
                        },
                    )
                )

        tasks = _assign_task_ids(candidate_tasks)
        plan = _compose_plan(
            plan_id=plan_id,
            created_at=created_at,
            tasks=tasks,
            events=(),
            metadata=dict(metadata or {}),
        )
        self._plans[plan_id] = plan
        return plan

    def apply_task_update(
        self,
        plan_id: str,
        *,
        task_id: str,
        actor_id: str,
        new_status: CorrectionTaskStatus | str,
        note: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ComplianceCorrectionPlan:
        plan = self.get_plan(plan_id)
        normalized_task_id = _normalize_required(task_id, "task_id")
        normalized_actor = _normalize_required(actor_id, "actor_id")
        normalized_status = _normalize_task_status(new_status)
        normalized_note = _normalize_optional(note)

        matched = False
        updated_tasks: list[ComplianceCorrectionTask] = []
        previous_status: str | None = None
        for task in plan.tasks:
            if task.task_id != normalized_task_id:
                updated_tasks.append(task)
                continue

            matched = True
            previous_status = task.status
            merged_metadata = dict(task.metadata)
            merged_metadata["last_updated_by"] = normalized_actor
            merged_metadata["last_updated_at"] = _utc_now_iso()
            if metadata:
                merged_metadata.update(dict(metadata))

            updated_tasks.append(
                replace(
                    task,
                    status=normalized_status,
                    metadata=merged_metadata,
                )
            )

        if not matched:
            raise KeyError(f"Unknown task_id {normalized_task_id} in plan {plan.plan_id}")

        event = ComplianceCorrectionEvent(
            event_id=f"{plan.plan_id}-evt-{len(plan.events) + 1:03d}",
            scope_id=normalized_task_id,
            actor_id=normalized_actor,
            action="task_status_updated",
            previous_status=previous_status,
            new_status=normalized_status,
            note=normalized_note,
            created_at=_utc_now_iso(),
            metadata=dict(metadata or {}),
        )

        updated = _compose_plan(
            plan_id=plan.plan_id,
            created_at=plan.created_at,
            tasks=tuple(updated_tasks),
            events=plan.events + (event,),
            metadata=dict(plan.metadata),
        )
        self._plans[plan.plan_id] = updated
        return updated

    def finalize_plan(
        self,
        plan_id: str,
        *,
        actor_id: str,
        note: str,
    ) -> ComplianceCorrectionPlan:
        plan = self.get_plan(plan_id)
        normalized_actor = _normalize_required(actor_id, "actor_id")
        normalized_note = _normalize_required(note, "note")

        unresolved = [task for task in plan.tasks if task.status in {"open", "in_progress"}]
        if unresolved:
            raise ComplianceCorrectionError(
                f"Cannot finalize {plan.plan_id} with unresolved tasks: {', '.join(task.task_id for task in unresolved)}"
            )

        merged_metadata = dict(plan.metadata)
        merged_metadata["finalized_by"] = normalized_actor
        merged_metadata["finalized_at"] = _utc_now_iso()
        merged_metadata["finalization_note"] = normalized_note

        event = ComplianceCorrectionEvent(
            event_id=f"{plan.plan_id}-evt-{len(plan.events) + 1:03d}",
            scope_id=plan.plan_id,
            actor_id=normalized_actor,
            action="plan_finalized",
            previous_status=plan.status,
            new_status="resolved",
            note=normalized_note,
            created_at=_utc_now_iso(),
            metadata={},
        )

        updated = _compose_plan(
            plan_id=plan.plan_id,
            created_at=plan.created_at,
            tasks=plan.tasks,
            events=plan.events + (event,),
            metadata=merged_metadata,
        )
        self._plans[plan.plan_id] = updated
        return updated

    def get_plan(self, plan_id: str) -> ComplianceCorrectionPlan:
        normalized_plan_id = _normalize_required(plan_id, "plan_id").lower()
        plan = self._plans.get(normalized_plan_id)
        if plan is None:
            raise KeyError(f"Unknown compliance correction plan: {normalized_plan_id}")
        return plan

    def list_plans(self, *, status: CorrectionPlanStatus | None = None) -> list[ComplianceCorrectionPlan]:
        plans = list(self._plans.values())
        if status is not None:
            normalized_status = _normalize_required(status, "status").lower()
            plans = [plan for plan in plans if plan.status == normalized_status]
        return sorted(plans, key=lambda item: item.plan_id)


def _assign_task_ids(tasks: list[ComplianceCorrectionTask]) -> tuple[ComplianceCorrectionTask, ...]:
    ordered = sorted(
        tasks,
        key=lambda item: (
            _priority_rank(item.priority),
            item.source_type,
            item.component_id,
            item.source_id,
        ),
        reverse=True,
    )

    assigned: list[ComplianceCorrectionTask] = []
    for index, task in enumerate(ordered, start=1):
        normalized_component = "".join(
            ch if ch.isalnum() else "_" for ch in task.component_id.lower()
        ).strip("_")
        assigned.append(
            replace(
                task,
                task_id=f"CT-{index:03d}-{normalized_component[:24] or 'component'}",
                component_id=task.component_id.lower(),
                reason=_normalize_required(task.reason, "reason"),
                recommended_action=_normalize_required(task.recommended_action, "recommended_action"),
            )
        )

    return tuple(sorted(assigned, key=lambda item: item.task_id))


def _compose_plan(
    *,
    plan_id: str,
    created_at: str,
    tasks: tuple[ComplianceCorrectionTask, ...],
    events: tuple[ComplianceCorrectionEvent, ...],
    metadata: dict[str, Any],
) -> ComplianceCorrectionPlan:
    open_count = sum(1 for task in tasks if task.status == "open")
    in_progress_count = sum(1 for task in tasks if task.status == "in_progress")
    resolved_count = sum(1 for task in tasks if task.status == "resolved")
    waived_count = sum(1 for task in tasks if task.status == "waived")
    critical_count = sum(1 for task in tasks if task.priority == "critical")
    high_count = sum(1 for task in tasks if task.priority == "high")

    if open_count + in_progress_count > 0:
        status: CorrectionPlanStatus = "open"
    else:
        status = "resolved"

    digest = _build_plan_digest(
        plan_id=plan_id,
        tasks=tasks,
        events=events,
        status=status,
    )

    return ComplianceCorrectionPlan(
        plan_id=plan_id,
        created_at=created_at,
        status=status,
        task_count=len(tasks),
        open_count=open_count,
        in_progress_count=in_progress_count,
        resolved_count=resolved_count,
        waived_count=waived_count,
        critical_count=critical_count,
        high_count=high_count,
        deterministic_digest=digest,
        tasks=tuple(sorted(tasks, key=lambda item: item.task_id)),
        events=tuple(sorted(events, key=lambda item: item.event_id)),
        metadata=dict(metadata),
    )


def _build_plan_digest(
    *,
    plan_id: str,
    tasks: tuple[ComplianceCorrectionTask, ...],
    events: tuple[ComplianceCorrectionEvent, ...],
    status: CorrectionPlanStatus,
) -> str:
    canonical = json.dumps(
        {
            "plan_id": plan_id,
            "status": status,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "source_type": task.source_type,
                    "source_id": task.source_id,
                    "component_id": task.component_id,
                    "priority": task.priority,
                    "status": task.status,
                    "reason": task.reason,
                    "recommended_action": task.recommended_action,
                }
                for task in sorted(tasks, key=lambda item: item.task_id)
            ],
            "events": [
                {
                    "event_id": event.event_id,
                    "scope_id": event.scope_id,
                    "action": event.action,
                    "previous_status": event.previous_status,
                    "new_status": event.new_status,
                    "note": event.note,
                    "created_at": event.created_at,
                }
                for event in sorted(events, key=lambda item: item.event_id)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


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


def _recommended_action_for_check(check_id: str) -> str:
    mapping = {
        "sample-coverage": "Add representative interaction samples to restore compliance evidence coverage.",
        "addressing-consistency": "Reinforce addressing policy and add regression checks for profile-specific address resolution.",
        "persona-tag": "Fix response tagging so persona tags are consistently attached to generated responses.",
        "confidence-tag": "Enforce confidence-tag rendering for all response formatter paths.",
    }
    return mapping.get(
        check_id,
        "Investigate root cause and apply corrective controls for this compliance signal.",
    )


def _priority_rank(priority: CorrectionTaskPriority) -> int:
    ranks = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    return ranks[priority]


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ComplianceCorrectionError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _normalize_task_status(value: CorrectionTaskStatus | str) -> CorrectionTaskStatus:
    normalized = _normalize_required(str(value), "new_status").lower()
    if normalized not in {"open", "in_progress", "resolved", "waived"}:
        raise ComplianceCorrectionError(
            "Unsupported task status " + normalized + ". Allowed: open, in_progress, resolved, waived"
        )
    return normalized  # type: ignore[return-value]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "CorrectionTaskPriority",
    "CorrectionTaskStatus",
    "CorrectionPlanStatus",
    "ComplianceCorrectionTask",
    "ComplianceCorrectionEvent",
    "ComplianceCorrectionPlan",
    "ComplianceCorrectionError",
    "ComplianceCorrectionWorkflow",
]
