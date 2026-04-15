"""Operator runbook finalization for incident playbook readiness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from runtime.orchestration.failure_injection_drills import (
    FailureInjectionScenario,
    default_failure_injection_scenarios,
)
from runtime.security.incident_playbooks import (
    IncidentPlaybookDefinition,
    IncidentPlaybookManager,
    build_default_incident_playbooks,
)

RunbookPriority = Literal["p1", "p2", "p3"]
RunbookPhase = Literal["containment", "recovery"]

_REQUIRED_PLAYBOOK_IDS = {
    "incident.prompt_injection",
    "incident.secret_exposure",
    "incident.policy_anomaly",
}


@dataclass(frozen=True)
class OperatorRunbookStep:
    step_id: str
    phase: RunbookPhase
    action: str
    required: bool
    instruction: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OperatorRunbookDocument:
    runbook_id: str
    title: str
    incident_playbook_id: str
    trigger_signals: tuple[str, ...]
    priority: RunbookPriority
    escalation_targets: tuple[str, ...]
    steps: tuple[OperatorRunbookStep, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OperatorRunbookBundle:
    bundle_id: str
    bundle_version: str
    generated_at: str
    incident_playbook_ids: tuple[str, ...]
    critical_service_ids: tuple[str, ...]
    documents: tuple[OperatorRunbookDocument, ...]
    deterministic_digest: str
    markdown: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "generated_at": self.generated_at,
            "incident_playbook_ids": list(self.incident_playbook_ids),
            "critical_service_ids": list(self.critical_service_ids),
            "documents": [
                {
                    "runbook_id": document.runbook_id,
                    "title": document.title,
                    "incident_playbook_id": document.incident_playbook_id,
                    "trigger_signals": list(document.trigger_signals),
                    "priority": document.priority,
                    "escalation_targets": list(document.escalation_targets),
                    "steps": [
                        {
                            "step_id": step.step_id,
                            "phase": step.phase,
                            "action": step.action,
                            "required": step.required,
                            "instruction": step.instruction,
                            "metadata": dict(step.metadata),
                        }
                        for step in sorted(
                            document.steps,
                            key=lambda item: (item.phase, item.step_id),
                        )
                    ],
                    "metadata": dict(document.metadata),
                }
                for document in sorted(self.documents, key=lambda item: item.runbook_id)
            ],
            "deterministic_digest": self.deterministic_digest,
            "markdown": self.markdown,
            "metadata": dict(self.metadata),
        }


class OperatorRunbookError(ValueError):
    """Raised when operator runbook finalization inputs are incomplete or invalid."""


class OperatorRunbookFinalizer:
    """Finalizes operator runbook artifacts from incident playbooks and drill coverage."""

    def finalize_bundle(
        self,
        incident_manager: IncidentPlaybookManager,
        *,
        failure_scenarios: tuple[FailureInjectionScenario, ...] | None = None,
        bundle_version: str = "1.0.0",
        metadata: dict[str, Any] | None = None,
    ) -> OperatorRunbookBundle:
        if not isinstance(incident_manager, IncidentPlaybookManager):
            raise TypeError("incident_manager must be IncidentPlaybookManager")

        playbooks = incident_manager.list_playbooks()
        if not playbooks:
            raise OperatorRunbookError("incident_manager must include at least one playbook")

        playbook_ids = tuple(sorted(playbook.playbook_id for playbook in playbooks))
        missing_required = sorted(_REQUIRED_PLAYBOOK_IDS - set(playbook_ids))
        if missing_required:
            raise OperatorRunbookError(
                "incident playbook coverage missing required ids: " + ", ".join(missing_required)
            )

        if failure_scenarios is None:
            failure_scenarios = default_failure_injection_scenarios()
        normalized_scenarios = _normalize_scenarios(failure_scenarios)
        critical_service_ids = tuple(
            sorted({scenario.service_id for scenario in normalized_scenarios})
        )
        if not critical_service_ids:
            raise OperatorRunbookError("failure_scenarios must include at least one service")

        documents = tuple(
            self._build_document(playbook)
            for playbook in sorted(playbooks, key=lambda item: item.playbook_id)
        )

        deterministic_digest = _build_bundle_digest(
            bundle_version=bundle_version,
            incident_playbook_ids=playbook_ids,
            critical_service_ids=critical_service_ids,
            documents=documents,
        )

        bundle_id = f"operator-runbooks-{deterministic_digest[:20]}"
        markdown = _render_markdown(
            bundle_id=bundle_id,
            bundle_version=bundle_version,
            critical_service_ids=critical_service_ids,
            documents=documents,
        )

        return OperatorRunbookBundle(
            bundle_id=bundle_id,
            bundle_version=_normalize_required(bundle_version, "bundle_version"),
            generated_at=_utc_now_iso(),
            incident_playbook_ids=playbook_ids,
            critical_service_ids=critical_service_ids,
            documents=documents,
            deterministic_digest=deterministic_digest,
            markdown=markdown,
            metadata=dict(metadata or {}),
        )

    def _build_document(self, playbook: IncidentPlaybookDefinition) -> OperatorRunbookDocument:
        runbook_id = f"runbook.{playbook.playbook_id}"
        trigger_signals = tuple(sorted(playbook.trigger_signals))

        steps: list[OperatorRunbookStep] = []
        for step in playbook.containment_steps:
            steps.append(
                OperatorRunbookStep(
                    step_id=step.step_id,
                    phase="containment",
                    action=step.action,
                    required=step.required,
                    instruction=self._build_instruction(step.action, step.parameters),
                    metadata={"playbook_phase": "containment"},
                )
            )
        for step in playbook.recovery_steps:
            steps.append(
                OperatorRunbookStep(
                    step_id=step.step_id,
                    phase="recovery",
                    action=step.action,
                    required=step.required,
                    instruction=self._build_instruction(step.action, step.parameters),
                    metadata={"playbook_phase": "recovery"},
                )
            )

        priority = _derive_priority(playbook.playbook_id)
        escalation_targets = _derive_escalation_targets(priority)

        return OperatorRunbookDocument(
            runbook_id=runbook_id,
            title=f"Operator {playbook.name}",
            incident_playbook_id=playbook.playbook_id,
            trigger_signals=trigger_signals,
            priority=priority,
            escalation_targets=escalation_targets,
            steps=tuple(steps),
            metadata={
                "step_count": len(steps),
                "trigger_count": len(trigger_signals),
                **dict(playbook.metadata),
            },
        )

    @staticmethod
    def _build_instruction(action: str, parameters: dict[str, Any]) -> str:
        if not parameters:
            return f"Execute action '{action}' with standard operating context."
        serialized = json.dumps(parameters, sort_keys=True, separators=(",", ":"), default=str)
        return f"Execute action '{action}' with parameters {serialized}."


def build_default_operator_runbook_bundle() -> OperatorRunbookBundle:
    manager = build_default_incident_playbooks()
    return OperatorRunbookFinalizer().finalize_bundle(
        manager,
        failure_scenarios=default_failure_injection_scenarios(),
        metadata={"phase": "P11-T11", "source": "default_bundle"},
    )


def _normalize_scenarios(
    scenarios: tuple[FailureInjectionScenario, ...] | list[FailureInjectionScenario],
) -> tuple[FailureInjectionScenario, ...]:
    if not isinstance(scenarios, (tuple, list)):
        raise TypeError("failure_scenarios must be tuple or list of FailureInjectionScenario")

    normalized: list[FailureInjectionScenario] = []
    seen_ids: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, FailureInjectionScenario):
            raise TypeError("failure_scenarios must contain FailureInjectionScenario values")

        scenario_id = _normalize_required(scenario.scenario_id, "scenario_id").lower()
        if scenario_id in seen_ids:
            raise OperatorRunbookError(f"Duplicate failure scenario_id: {scenario_id}")
        seen_ids.add(scenario_id)

        normalized.append(
            FailureInjectionScenario(
                scenario_id=scenario_id,
                title=_normalize_required(scenario.title, "title"),
                service_id=_normalize_required(scenario.service_id, "service_id").lower(),
                fault_type=scenario.fault_type,
                severity=scenario.severity,
                target_response_seconds=float(scenario.target_response_seconds),
                expected_outcomes=tuple(sorted(scenario.expected_outcomes)),
                metadata=dict(scenario.metadata),
            )
        )

    return tuple(sorted(normalized, key=lambda item: item.scenario_id))


def _derive_priority(playbook_id: str) -> RunbookPriority:
    if "secret_exposure" in playbook_id:
        return "p1"
    if "prompt_injection" in playbook_id:
        return "p1"
    if "policy_anomaly" in playbook_id:
        return "p2"
    return "p3"


def _derive_escalation_targets(priority: RunbookPriority) -> tuple[str, ...]:
    if priority == "p1":
        return ("secops", "platform_oncall")
    if priority == "p2":
        return ("platform_oncall",)
    return ("service_owner",)


def _build_bundle_digest(
    *,
    bundle_version: str,
    incident_playbook_ids: tuple[str, ...],
    critical_service_ids: tuple[str, ...],
    documents: tuple[OperatorRunbookDocument, ...],
) -> str:
    canonical = json.dumps(
        {
            "bundle_version": bundle_version,
            "incident_playbook_ids": list(incident_playbook_ids),
            "critical_service_ids": list(critical_service_ids),
            "documents": [
                {
                    "runbook_id": document.runbook_id,
                    "incident_playbook_id": document.incident_playbook_id,
                    "trigger_signals": list(document.trigger_signals),
                    "priority": document.priority,
                    "escalation_targets": list(document.escalation_targets),
                    "steps": [
                        {
                            "step_id": step.step_id,
                            "phase": step.phase,
                            "action": step.action,
                            "required": step.required,
                            "instruction": step.instruction,
                        }
                        for step in sorted(
                            document.steps,
                            key=lambda item: (item.phase, item.step_id),
                        )
                    ],
                }
                for document in sorted(documents, key=lambda item: item.runbook_id)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _render_markdown(
    *,
    bundle_id: str,
    bundle_version: str,
    critical_service_ids: tuple[str, ...],
    documents: tuple[OperatorRunbookDocument, ...],
) -> str:
    lines = [
        f"# Operator Runbook Bundle {bundle_id}",
        "",
        f"Version: {bundle_version}",
        f"Critical services covered: {', '.join(critical_service_ids)}",
        "",
    ]

    for document in sorted(documents, key=lambda item: item.runbook_id):
        lines.append(f"## {document.title}")
        lines.append(f"Runbook ID: {document.runbook_id}")
        lines.append(f"Priority: {document.priority}")
        lines.append(f"Escalation: {', '.join(document.escalation_targets)}")
        lines.append(f"Triggers: {', '.join(document.trigger_signals)}")
        lines.append("")
        lines.append("Steps:")
        for step in document.steps:
            requirement = "required" if step.required else "optional"
            lines.append(
                f"- [{step.phase}] {step.step_id} ({requirement}): {step.instruction}"
            )
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise OperatorRunbookError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "RunbookPriority",
    "RunbookPhase",
    "OperatorRunbookStep",
    "OperatorRunbookDocument",
    "OperatorRunbookBundle",
    "OperatorRunbookError",
    "OperatorRunbookFinalizer",
    "build_default_operator_runbook_bundle",
]
