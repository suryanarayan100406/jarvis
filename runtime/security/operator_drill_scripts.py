"""Operator drill scripts for emergency response readiness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import uuid4

from .incident_playbooks import IncidentPlaybookExecutionResult, IncidentPlaybookManager

DrillStatus = Literal["pass", "fail", "degraded"]


@dataclass(frozen=True)
class OperatorDrillScenario:
    drill_id: str
    title: str
    incident_id: str
    playbook_id: str
    target_response_seconds: float
    expected_status: tuple[str, ...]
    trigger_signals: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OperatorDrillResult:
    drill_id: str
    title: str
    status: DrillStatus
    playbook_status: str
    response_seconds: float
    target_response_seconds: float
    reason: str
    incident_id: str
    playbook_id: str
    execution_id: str
    trigger_signals: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OperatorReadinessReport:
    started_at: str
    finished_at: str
    total_drills: int
    passed: int
    degraded: int
    failed: int
    readiness_score: float
    summary: str
    drills: tuple[OperatorDrillResult, ...]


class OperatorDrillError(ValueError):
    """Raised when emergency drill scripts receive invalid input."""


class OperatorEmergencyDrillRunner:
    """Runs emergency operator drills using incident playbooks and readiness scoring."""

    def __init__(
        self,
        playbook_manager: IncidentPlaybookManager,
        *,
        timer: Callable[[], float],
    ) -> None:
        self.playbook_manager = playbook_manager
        self._timer = timer

    def run_drills(
        self,
        scenarios: list[OperatorDrillScenario] | tuple[OperatorDrillScenario, ...],
        *,
        context: dict[str, Any] | None = None,
    ) -> OperatorReadinessReport:
        """Execute drill scenarios and return readiness metrics."""
        normalized_scenarios = [self._normalize_scenario(scenario) for scenario in scenarios]
        if not normalized_scenarios:
            raise OperatorDrillError("At least one drill scenario is required")

        started_at = _utc_now_iso()
        shared_context = dict(context or {})
        results: list[OperatorDrillResult] = []

        for scenario in normalized_scenarios:
            execution_context = dict(shared_context)
            execution_context["drill_id"] = scenario.drill_id
            execution_context["trigger_signals"] = list(scenario.trigger_signals)

            start = self._timer()
            execution = self.playbook_manager.execute_playbook(
                scenario.playbook_id,
                incident_id=scenario.incident_id,
                context=execution_context,
                execution_id=f"drill-{uuid4().hex[:12]}",
            )
            elapsed = max(0.0, self._timer() - start)

            result = self._evaluate_result(scenario, execution, elapsed)
            results.append(result)

        passed = sum(1 for item in results if item.status == "pass")
        degraded = sum(1 for item in results if item.status == "degraded")
        failed = sum(1 for item in results if item.status == "fail")
        total = len(results)

        score = round(((passed + (0.5 * degraded)) / total), 4)
        summary = self._build_summary(total=total, passed=passed, degraded=degraded, failed=failed, score=score)
        return OperatorReadinessReport(
            started_at=started_at,
            finished_at=_utc_now_iso(),
            total_drills=total,
            passed=passed,
            degraded=degraded,
            failed=failed,
            readiness_score=score,
            summary=summary,
            drills=tuple(results),
        )

    def _evaluate_result(
        self,
        scenario: OperatorDrillScenario,
        execution: IncidentPlaybookExecutionResult,
        elapsed: float,
    ) -> OperatorDrillResult:
        target = scenario.target_response_seconds
        within_budget = elapsed <= target
        status: DrillStatus
        reason: str

        if execution.status in scenario.expected_status and within_budget:
            status = "pass"
            reason = "Playbook reached expected status within response target."
        elif execution.status in scenario.expected_status and not within_budget:
            status = "degraded"
            reason = "Playbook reached expected status but exceeded response target."
        elif execution.status == "degraded":
            status = "degraded"
            reason = "Playbook completed in degraded mode; operator remediation required."
        else:
            status = "fail"
            reason = "Playbook did not meet expected incident response outcome."

        return OperatorDrillResult(
            drill_id=scenario.drill_id,
            title=scenario.title,
            status=status,
            playbook_status=execution.status,
            response_seconds=round(elapsed, 6),
            target_response_seconds=target,
            reason=reason,
            incident_id=scenario.incident_id,
            playbook_id=scenario.playbook_id,
            execution_id=execution.execution_id,
            trigger_signals=scenario.trigger_signals,
            metadata=dict(scenario.metadata),
        )

    @staticmethod
    def _normalize_scenario(scenario: OperatorDrillScenario) -> OperatorDrillScenario:
        drill_id = _normalize_required(scenario.drill_id, "drill_id")
        title = _normalize_required(scenario.title, "title")
        incident_id = _normalize_required(scenario.incident_id, "incident_id")
        playbook_id = _normalize_required(scenario.playbook_id, "playbook_id")

        if scenario.target_response_seconds <= 0:
            raise OperatorDrillError("target_response_seconds must be greater than zero")

        expected_status = tuple(
            sorted({_normalize_required(item, "expected_status").lower() for item in scenario.expected_status})
        )
        if not expected_status:
            raise OperatorDrillError("expected_status must include at least one status")

        allowed_status = {"contained", "recovered", "degraded", "failed"}
        unknown = [item for item in expected_status if item not in allowed_status]
        if unknown:
            raise OperatorDrillError(f"Unsupported expected status values: {', '.join(unknown)}")

        trigger_signals = tuple(
            sorted({_normalize_required(signal, "trigger_signal").lower() for signal in scenario.trigger_signals})
        )
        if not trigger_signals:
            raise OperatorDrillError("trigger_signals must include at least one signal")

        return OperatorDrillScenario(
            drill_id=drill_id,
            title=title,
            incident_id=incident_id,
            playbook_id=playbook_id,
            target_response_seconds=float(scenario.target_response_seconds),
            expected_status=expected_status,
            trigger_signals=trigger_signals,
            metadata=dict(scenario.metadata),
        )

    @staticmethod
    def _build_summary(*, total: int, passed: int, degraded: int, failed: int, score: float) -> str:
        return (
            f"Operator readiness: {passed}/{total} drills passed, "
            f"{degraded} degraded, {failed} failed, score={score:.2f}."
        )


def default_operator_drill_scenarios() -> tuple[OperatorDrillScenario, ...]:
    """Return baseline emergency response drill scenarios."""
    return (
        OperatorDrillScenario(
            drill_id="drill-prompt-injection",
            title="Prompt injection containment",
            incident_id="drill-inc-001",
            playbook_id="incident.prompt_injection",
            target_response_seconds=0.5,
            expected_status=("recovered", "degraded"),
            trigger_signals=("prompt_injection_attempt", "identity_override_attempt"),
            metadata={"severity": "high", "owner": "secops"},
        ),
        OperatorDrillScenario(
            drill_id="drill-secret-exposure",
            title="Secret exposure recovery",
            incident_id="drill-inc-002",
            playbook_id="incident.secret_exposure",
            target_response_seconds=0.5,
            expected_status=("recovered",),
            trigger_signals=("credential_harvest", "sensitive_payload_request"),
            metadata={"severity": "critical", "owner": "secops"},
        ),
        OperatorDrillScenario(
            drill_id="drill-policy-anomaly",
            title="Policy anomaly isolation",
            incident_id="drill-inc-003",
            playbook_id="incident.policy_anomaly",
            target_response_seconds=0.5,
            expected_status=("recovered", "degraded"),
            trigger_signals=("deny_burst_pattern", "privilege_escalation_pattern"),
            metadata={"severity": "high", "owner": "platform-ops"},
        ),
    )


def create_drill_runner_with_default_playbooks(
    *,
    handlers: dict[str, Callable[[Any, dict[str, Any]], Any]],
    timer: Callable[[], float],
) -> OperatorEmergencyDrillRunner:
    """Build a drill runner with default incident playbooks and provided handlers."""
    manager = IncidentPlaybookManager(handlers=handlers)

    from .incident_playbooks import build_default_incident_playbooks

    build_default_incident_playbooks(manager)
    return OperatorEmergencyDrillRunner(manager, timer=timer)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise OperatorDrillError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "OperatorDrillError",
    "OperatorDrillResult",
    "OperatorDrillScenario",
    "OperatorEmergencyDrillRunner",
    "OperatorReadinessReport",
    "create_drill_runner_with_default_playbooks",
    "default_operator_drill_scenarios",
]
