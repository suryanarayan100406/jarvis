"""Incident playbooks for security containment and recovery workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import uuid4

IncidentPlaybookHandler = Callable[["IncidentPlaybookStep", dict[str, Any]], Any]

ExecutionStatus = Literal["contained", "recovered", "degraded", "failed"]
StepStatus = Literal["success", "failed"]
StepPhase = Literal["containment", "recovery"]


@dataclass(frozen=True)
class IncidentPlaybookStep:
    step_id: str
    action: str
    parameters: dict[str, Any]
    required: bool = True


@dataclass(frozen=True)
class IncidentPlaybookDefinition:
    playbook_id: str
    name: str
    trigger_signals: tuple[str, ...]
    containment_steps: tuple[IncidentPlaybookStep, ...]
    recovery_steps: tuple[IncidentPlaybookStep, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class IncidentStepOutcome:
    step_id: str
    phase: StepPhase
    action: str
    required: bool
    status: StepStatus
    output: Any
    error: str | None
    started_at: str
    finished_at: str


@dataclass(frozen=True)
class IncidentPlaybookExecutionResult:
    execution_id: str
    playbook_id: str
    incident_id: str
    status: ExecutionStatus
    outcomes: tuple[IncidentStepOutcome, ...]
    started_at: str
    finished_at: str
    metrics: dict[str, int]


class IncidentPlaybookError(ValueError):
    """Raised when incident playbook workflows receive invalid inputs."""


class IncidentPlaybookManager:
    """Registers and executes deterministic incident playbooks."""

    def __init__(self, handlers: dict[str, IncidentPlaybookHandler] | None = None) -> None:
        self.handlers = dict(handlers or {})
        self._playbooks: dict[str, IncidentPlaybookDefinition] = {}

    def register_handler(self, action: str, handler: IncidentPlaybookHandler) -> None:
        normalized_action = _normalize_required(action, "action")
        self.handlers[normalized_action] = handler

    def register_playbook(
        self,
        *,
        playbook_id: str,
        name: str,
        containment_steps: list[IncidentPlaybookStep] | tuple[IncidentPlaybookStep, ...],
        recovery_steps: list[IncidentPlaybookStep] | tuple[IncidentPlaybookStep, ...],
        trigger_signals: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IncidentPlaybookDefinition:
        normalized_id = _normalize_required(playbook_id, "playbook_id")
        if normalized_id in self._playbooks:
            raise IncidentPlaybookError(f"Playbook already exists: {normalized_id}")

        normalized_name = _normalize_required(name, "name")
        normalized_containment = tuple(self._normalize_step(step) for step in containment_steps)
        normalized_recovery = tuple(self._normalize_step(step) for step in recovery_steps)

        if not normalized_containment:
            raise IncidentPlaybookError("containment_steps must include at least one step")
        if not normalized_recovery:
            raise IncidentPlaybookError("recovery_steps must include at least one step")

        signals = _normalize_signals(trigger_signals or ())
        definition = IncidentPlaybookDefinition(
            playbook_id=normalized_id,
            name=normalized_name,
            trigger_signals=signals,
            containment_steps=normalized_containment,
            recovery_steps=normalized_recovery,
            metadata=dict(metadata or {}),
        )
        self._playbooks[normalized_id] = definition
        return definition

    def get_playbook(self, playbook_id: str) -> IncidentPlaybookDefinition:
        normalized_id = _normalize_required(playbook_id, "playbook_id")
        playbook = self._playbooks.get(normalized_id)
        if playbook is None:
            raise KeyError(f"Unknown playbook: {normalized_id}")
        return playbook

    def list_playbooks(self) -> list[IncidentPlaybookDefinition]:
        playbooks = list(self._playbooks.values())
        playbooks.sort(key=lambda item: item.playbook_id)
        return playbooks

    def recommend_playbooks(self, trigger_signal: str) -> list[IncidentPlaybookDefinition]:
        normalized_signal = _normalize_required(trigger_signal, "trigger_signal").lower()
        matches = [
            playbook
            for playbook in self._playbooks.values()
            if normalized_signal in playbook.trigger_signals
        ]
        matches.sort(key=lambda item: (len(item.containment_steps) + len(item.recovery_steps), item.playbook_id), reverse=True)
        return matches

    def execute_playbook(
        self,
        playbook_id: str,
        *,
        incident_id: str,
        context: dict[str, Any] | None = None,
        execution_id: str | None = None,
        stop_after_containment: bool = False,
    ) -> IncidentPlaybookExecutionResult:
        playbook = self.get_playbook(playbook_id)
        normalized_incident = _normalize_required(incident_id, "incident_id")
        assigned_execution_id = _normalize_required(execution_id or str(uuid4()), "execution_id")

        started_at = _utc_now_iso()
        execution_context = dict(context or {})
        execution_context["incident_id"] = normalized_incident
        execution_context["playbook_id"] = playbook.playbook_id
        execution_context["execution_id"] = assigned_execution_id

        outcomes: list[IncidentStepOutcome] = []
        degraded = False

        for step in playbook.containment_steps:
            outcome = self._execute_step(step=step, phase="containment", context=execution_context)
            outcomes.append(outcome)
            if outcome.status == "success":
                execution_context[f"step:{step.step_id}"] = outcome.output
                continue

            if step.required:
                return self._build_result(
                    execution_id=assigned_execution_id,
                    playbook_id=playbook.playbook_id,
                    incident_id=normalized_incident,
                    status="failed",
                    outcomes=outcomes,
                    started_at=started_at,
                )

            degraded = True

        if stop_after_containment:
            return self._build_result(
                execution_id=assigned_execution_id,
                playbook_id=playbook.playbook_id,
                incident_id=normalized_incident,
                status="degraded" if degraded else "contained",
                outcomes=outcomes,
                started_at=started_at,
            )

        for step in playbook.recovery_steps:
            outcome = self._execute_step(step=step, phase="recovery", context=execution_context)
            outcomes.append(outcome)
            if outcome.status == "success":
                execution_context[f"step:{step.step_id}"] = outcome.output
                continue

            if step.required:
                return self._build_result(
                    execution_id=assigned_execution_id,
                    playbook_id=playbook.playbook_id,
                    incident_id=normalized_incident,
                    status="failed",
                    outcomes=outcomes,
                    started_at=started_at,
                )

            degraded = True

        return self._build_result(
            execution_id=assigned_execution_id,
            playbook_id=playbook.playbook_id,
            incident_id=normalized_incident,
            status="degraded" if degraded else "recovered",
            outcomes=outcomes,
            started_at=started_at,
        )

    def _execute_step(
        self,
        *,
        step: IncidentPlaybookStep,
        phase: StepPhase,
        context: dict[str, Any],
    ) -> IncidentStepOutcome:
        started_at = _utc_now_iso()
        handler = self.handlers.get(step.action)
        if handler is None:
            return IncidentStepOutcome(
                step_id=step.step_id,
                phase=phase,
                action=step.action,
                required=step.required,
                status="failed",
                output=None,
                error=f"No handler registered for action: {step.action}",
                started_at=started_at,
                finished_at=_utc_now_iso(),
            )

        try:
            output = handler(step, context)
        except Exception as exc:  # pragma: no cover - boundary guard
            return IncidentStepOutcome(
                step_id=step.step_id,
                phase=phase,
                action=step.action,
                required=step.required,
                status="failed",
                output=None,
                error=str(exc),
                started_at=started_at,
                finished_at=_utc_now_iso(),
            )

        return IncidentStepOutcome(
            step_id=step.step_id,
            phase=phase,
            action=step.action,
            required=step.required,
            status="success",
            output=output,
            error=None,
            started_at=started_at,
            finished_at=_utc_now_iso(),
        )

    @staticmethod
    def _normalize_step(step: IncidentPlaybookStep) -> IncidentPlaybookStep:
        return IncidentPlaybookStep(
            step_id=_normalize_required(step.step_id, "step_id"),
            action=_normalize_required(step.action, "action"),
            parameters=dict(step.parameters),
            required=bool(step.required),
        )

    @staticmethod
    def _build_result(
        *,
        execution_id: str,
        playbook_id: str,
        incident_id: str,
        status: ExecutionStatus,
        outcomes: list[IncidentStepOutcome],
        started_at: str,
    ) -> IncidentPlaybookExecutionResult:
        success_count = sum(1 for outcome in outcomes if outcome.status == "success")
        failure_count = sum(1 for outcome in outcomes if outcome.status == "failed")
        containment_count = sum(1 for outcome in outcomes if outcome.phase == "containment")
        recovery_count = sum(1 for outcome in outcomes if outcome.phase == "recovery")
        required_failure_count = sum(1 for outcome in outcomes if outcome.status == "failed" and outcome.required)
        optional_failure_count = sum(1 for outcome in outcomes if outcome.status == "failed" and not outcome.required)

        return IncidentPlaybookExecutionResult(
            execution_id=execution_id,
            playbook_id=playbook_id,
            incident_id=incident_id,
            status=status,
            outcomes=tuple(outcomes),
            started_at=started_at,
            finished_at=_utc_now_iso(),
            metrics={
                "steps_total": len(outcomes),
                "steps_success": success_count,
                "steps_failed": failure_count,
                "containment_steps_executed": containment_count,
                "recovery_steps_executed": recovery_count,
                "required_failures": required_failure_count,
                "optional_failures": optional_failure_count,
            },
        )


def build_default_incident_playbooks(
    manager: IncidentPlaybookManager | None = None,
) -> IncidentPlaybookManager:
    """Create baseline incident playbooks for containment and recovery workflows."""
    registry = manager or IncidentPlaybookManager()

    blueprints = (
        {
            "playbook_id": "incident.prompt_injection",
            "name": "Prompt Injection Containment",
            "trigger_signals": (
                "prompt_injection_attempt",
                "identity_override_attempt",
                "policy_bypass_request",
            ),
            "containment_steps": (
                IncidentPlaybookStep(step_id="contain-1", action="isolate_session", parameters={}),
                IncidentPlaybookStep(step_id="contain-2", action="revoke_untrusted_tokens", parameters={}),
                IncidentPlaybookStep(step_id="contain-3", action="enforce_safe_mode", parameters={"mode": "restricted"}),
            ),
            "recovery_steps": (
                IncidentPlaybookStep(step_id="recover-1", action="reset_session_context", parameters={}),
                IncidentPlaybookStep(step_id="recover-2", action="run_security_review", parameters={"scope": "conversation"}),
            ),
        },
        {
            "playbook_id": "incident.secret_exposure",
            "name": "Secret Exposure Recovery",
            "trigger_signals": ("credential_harvest", "sensitive_payload_request"),
            "containment_steps": (
                IncidentPlaybookStep(step_id="contain-1", action="revoke_secret_access", parameters={}),
                IncidentPlaybookStep(step_id="contain-2", action="block_replay_surface", parameters={}),
            ),
            "recovery_steps": (
                IncidentPlaybookStep(step_id="recover-1", action="rotate_exposed_secrets", parameters={}),
                IncidentPlaybookStep(step_id="recover-2", action="verify_secret_integrity", parameters={}),
            ),
        },
        {
            "playbook_id": "incident.policy_anomaly",
            "name": "Policy Anomaly Response",
            "trigger_signals": ("deny_burst_pattern", "blocked_token_detected", "privilege_escalation_pattern"),
            "containment_steps": (
                IncidentPlaybookStep(step_id="contain-1", action="freeze_high_risk_actions", parameters={}),
                IncidentPlaybookStep(step_id="contain-2", action="open_escalation_ticket", parameters={}),
            ),
            "recovery_steps": (
                IncidentPlaybookStep(step_id="recover-1", action="rebaseline_policy", parameters={"mode": "strict"}),
                IncidentPlaybookStep(step_id="recover-2", action="verify_operator_intent", parameters={}, required=False),
            ),
        },
    )

    existing_ids = {playbook.playbook_id for playbook in registry.list_playbooks()}
    for blueprint in blueprints:
        if blueprint["playbook_id"] in existing_ids:
            continue
        registry.register_playbook(**blueprint)

    return registry


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise IncidentPlaybookError(f"{field_name} is required")
    return normalized


def _normalize_signals(signals: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = sorted({_normalize_required(signal, "signal").lower() for signal in signals})
    return tuple(normalized)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "IncidentPlaybookDefinition",
    "IncidentPlaybookError",
    "IncidentPlaybookExecutionResult",
    "IncidentPlaybookManager",
    "IncidentPlaybookStep",
    "IncidentStepOutcome",
    "build_default_incident_playbooks",
]
