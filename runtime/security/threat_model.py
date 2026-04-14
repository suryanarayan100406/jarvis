"""Threat model registry with prioritized abuse cases and mitigation mapping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ThreatMitigation:
    mitigation_id: str
    name: str
    description: str
    owner: str
    mapped_components: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ThreatAbuseCase:
    case_id: str
    title: str
    description: str
    attack_surface: str
    likelihood: int
    impact: int
    priority_score: int
    mitigation_ids: tuple[str, ...]
    detection_signals: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ThreatCaseMapping:
    case_id: str
    priority_score: int
    mitigation_ids: tuple[str, ...]
    missing_mitigation_ids: tuple[str, ...]


@dataclass(frozen=True)
class ThreatModelReport:
    generated_at: str
    total_cases: int
    total_mitigations: int
    prioritized_case_ids: tuple[str, ...]
    critical_case_ids: tuple[str, ...]
    uncovered_case_ids: tuple[str, ...]
    mitigation_coverage_ratio: float
    mappings: tuple[ThreatCaseMapping, ...]


class ThreatModelError(ValueError):
    """Raised when threat-model operations receive invalid data."""


class ThreatModelRegistry:
    """Stores threat abuse cases, maps mitigations, and produces prioritized reports."""

    def __init__(self) -> None:
        self._mitigations: dict[str, ThreatMitigation] = {}
        self._cases: dict[str, ThreatAbuseCase] = {}

    def register_mitigation(
        self,
        *,
        mitigation_id: str,
        name: str,
        description: str,
        owner: str,
        mapped_components: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ThreatMitigation:
        normalized_id = _normalize_required(mitigation_id, "mitigation_id")
        if normalized_id in self._mitigations:
            raise ThreatModelError(f"Mitigation already exists: {normalized_id}")

        mitigation = ThreatMitigation(
            mitigation_id=normalized_id,
            name=_normalize_required(name, "name"),
            description=_normalize_required(description, "description"),
            owner=_normalize_required(owner, "owner"),
            mapped_components=_normalize_tuple(mapped_components),
            metadata=dict(metadata or {}),
        )
        self._mitigations[normalized_id] = mitigation
        return mitigation

    def add_abuse_case(
        self,
        *,
        case_id: str,
        title: str,
        description: str,
        attack_surface: str,
        likelihood: int,
        impact: int,
        mitigation_ids: list[str] | tuple[str, ...] | None = None,
        detection_signals: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ThreatAbuseCase:
        normalized_id = _normalize_required(case_id, "case_id")
        if normalized_id in self._cases:
            raise ThreatModelError(f"Abuse case already exists: {normalized_id}")

        normalized_likelihood = _normalize_score(likelihood, "likelihood")
        normalized_impact = _normalize_score(impact, "impact")
        normalized_mitigations = _normalize_tuple(mitigation_ids)

        abuse_case = ThreatAbuseCase(
            case_id=normalized_id,
            title=_normalize_required(title, "title"),
            description=_normalize_required(description, "description"),
            attack_surface=_normalize_required(attack_surface, "attack_surface"),
            likelihood=normalized_likelihood,
            impact=normalized_impact,
            priority_score=normalized_likelihood * normalized_impact,
            mitigation_ids=normalized_mitigations,
            detection_signals=_normalize_tuple(detection_signals),
            metadata=dict(metadata or {}),
        )
        self._cases[normalized_id] = abuse_case
        return abuse_case

    def get_abuse_case(self, case_id: str) -> ThreatAbuseCase:
        normalized_id = _normalize_required(case_id, "case_id")
        abuse_case = self._cases.get(normalized_id)
        if abuse_case is None:
            raise KeyError(f"Unknown abuse case: {normalized_id}")
        return abuse_case

    def get_mitigation(self, mitigation_id: str) -> ThreatMitigation:
        normalized_id = _normalize_required(mitigation_id, "mitigation_id")
        mitigation = self._mitigations.get(normalized_id)
        if mitigation is None:
            raise KeyError(f"Unknown mitigation: {normalized_id}")
        return mitigation

    def list_abuse_cases(self, *, prioritized: bool = True) -> list[ThreatAbuseCase]:
        abuse_cases = list(self._cases.values())
        if prioritized:
            abuse_cases.sort(
                key=lambda item: (
                    -item.priority_score,
                    -item.impact,
                    -item.likelihood,
                    item.case_id,
                )
            )
        else:
            abuse_cases.sort(key=lambda item: item.case_id)
        return abuse_cases

    def list_mitigations(self) -> list[ThreatMitigation]:
        mitigations = list(self._mitigations.values())
        mitigations.sort(key=lambda item: item.mitigation_id)
        return mitigations

    def map_case_mitigations(self, case_id: str) -> list[ThreatMitigation]:
        abuse_case = self.get_abuse_case(case_id)
        mapped = [
            self._mitigations[mitigation_id]
            for mitigation_id in abuse_case.mitigation_ids
            if mitigation_id in self._mitigations
        ]
        mapped.sort(key=lambda item: item.mitigation_id)
        return mapped

    def finalize_report(self) -> ThreatModelReport:
        cases = self.list_abuse_cases(prioritized=True)
        prioritized_case_ids = tuple(case.case_id for case in cases)
        critical_case_ids = tuple(case.case_id for case in cases if case.priority_score >= 16)

        mappings: list[ThreatCaseMapping] = []
        uncovered: list[str] = []
        covered_count = 0

        for abuse_case in cases:
            missing = sorted(
                mitigation_id
                for mitigation_id in abuse_case.mitigation_ids
                if mitigation_id not in self._mitigations
            )
            if not abuse_case.mitigation_ids or missing:
                uncovered.append(abuse_case.case_id)
            else:
                covered_count += 1

            mappings.append(
                ThreatCaseMapping(
                    case_id=abuse_case.case_id,
                    priority_score=abuse_case.priority_score,
                    mitigation_ids=abuse_case.mitigation_ids,
                    missing_mitigation_ids=tuple(missing),
                )
            )

        total_cases = len(cases)
        coverage_ratio = round((covered_count / total_cases), 4) if total_cases else 1.0
        return ThreatModelReport(
            generated_at=_utc_now_iso(),
            total_cases=total_cases,
            total_mitigations=len(self._mitigations),
            prioritized_case_ids=prioritized_case_ids,
            critical_case_ids=critical_case_ids,
            uncovered_case_ids=tuple(uncovered),
            mitigation_coverage_ratio=coverage_ratio,
            mappings=tuple(mappings),
        )


def build_default_threat_model() -> ThreatModelRegistry:
    """Create a baseline threat model with priority abuse cases and mapped controls."""
    model = ThreatModelRegistry()

    model.register_mitigation(
        mitigation_id="mit.prompt_input_filter",
        name="Prompt Input Guard",
        description="Detect and block prompt-injection and embedded instruction attacks.",
        owner="security",
        mapped_components=("runtime.security.input_guard.PromptSecurityFilter",),
    )
    model.register_mitigation(
        mitigation_id="mit.identity_override_guard",
        name="Identity Override Guard",
        description="Detect identity override instructions and force deterministic identity anchors.",
        owner="security",
        mapped_components=(
            "runtime.security.input_guard.PromptSecurityFilter",
            "runtime.security.identity_override_guard.IdentityOverrideGuard",
        ),
    )
    model.register_mitigation(
        mitigation_id="mit.policy_overlay",
        name="Policy Overlay Enforcement",
        description="Enforce command and host-scope policy rules before execution.",
        owner="control-plane",
        mapped_components=("runtime.control_plane.policy_overlay",),
    )
    model.register_mitigation(
        mitigation_id="mit.audit_chain",
        name="Immutable Audit Chain",
        description="Preserve tamper-evident event evidence for post-incident response.",
        owner="audit",
        mapped_components=("runtime.audit.audit_writer",),
    )
    model.register_mitigation(
        mitigation_id="mit.replay_redaction",
        name="Replay Redaction",
        description="Prevent payload disclosure in replay surfaces through redaction filters.",
        owner="replay",
        mapped_components=("runtime.replay.replay_endpoint",),
    )
    model.register_mitigation(
        mitigation_id="mit.untrusted_execution_guardrail",
        name="Untrusted Execution Guardrail",
        description="Require scoped authorization tokens before executing instructions from untrusted content.",
        owner="security",
        mapped_components=("runtime.security.untrusted_execution_guard.UntrustedContentExecutionGuard",),
    )
    model.register_mitigation(
        mitigation_id="mit.social_engineering_detector",
        name="Social Engineering Detector",
        description="Detect coercive, secret-seeking, and authority-impersonation patterns in conversation flow.",
        owner="security",
        mapped_components=("runtime.security.social_engineering_detector.SocialEngineeringSignalDetector",),
    )
    model.register_mitigation(
        mitigation_id="mit.policy_anomaly_detector",
        name="Policy Anomaly Detector",
        description="Detect suspicious command-pattern anomalies and repeated deny-burst behavior.",
        owner="security",
        mapped_components=("runtime.security.policy_anomaly_detector.PolicyAnomalyDetector",),
    )
    model.register_mitigation(
        mitigation_id="mit.incident_playbooks",
        name="Incident Playbooks",
        description="Execute deterministic containment and recovery workflows when incidents are detected.",
        owner="security",
        mapped_components=("runtime.security.incident_playbooks.IncidentPlaybookManager",),
    )
    model.register_mitigation(
        mitigation_id="mit.forensic_event_export",
        name="Forensic Event Export",
        description="Export deterministic incident evidence bundles for post-incident analysis.",
        owner="security",
        mapped_components=("runtime.security.forensic_event_export.ForensicEventExporter",),
    )

    model.add_abuse_case(
        case_id="abuse.prompt_injection",
        title="Prompt injection through user or external content",
        description="Attacker injects instructions to override policy or reveal hidden context.",
        attack_surface="conversation",
        likelihood=5,
        impact=5,
        mitigation_ids=(
            "mit.prompt_input_filter",
            "mit.untrusted_execution_guardrail",
            "mit.policy_overlay",
            "mit.incident_playbooks",
            "mit.audit_chain",
        ),
        detection_signals=("prompt_injection_attempt", "unsafe_instruction_pattern"),
    )
    model.add_abuse_case(
        case_id="abuse.identity_override",
        title="Identity override and persona hijack",
        description="Attacker attempts to force identity drift away from trusted directives.",
        attack_surface="conversation",
        likelihood=4,
        impact=5,
        mitigation_ids=("mit.identity_override_guard", "mit.incident_playbooks", "mit.audit_chain"),
        detection_signals=("identity_override_attempt",),
    )
    model.add_abuse_case(
        case_id="abuse.exfiltration_via_replay",
        title="Sensitive output exfiltration via replay channels",
        description="Attacker requests replay output to extract secrets or sensitive payloads.",
        attack_surface="replay",
        likelihood=3,
        impact=4,
        mitigation_ids=("mit.replay_redaction", "mit.forensic_event_export", "mit.audit_chain"),
        detection_signals=("sensitive_payload_request",),
    )
    model.add_abuse_case(
        case_id="abuse.policy_bypass",
        title="Policy bypass through command templating or host misuse",
        description="Attacker crafts operations to evade allowlists and execute blocked actions.",
        attack_surface="control-plane",
        likelihood=3,
        impact=5,
        mitigation_ids=("mit.policy_overlay", "mit.policy_anomaly_detector", "mit.incident_playbooks", "mit.audit_chain"),
        detection_signals=("blocked_token_detected", "deny_rule_triggered", "deny_burst_pattern"),
    )
    model.add_abuse_case(
        case_id="abuse.social_engineering",
        title="Social engineering through coercive conversation flow",
        description="Attacker pressures operators using urgency, impersonation, secrecy, and credential requests.",
        attack_surface="conversation",
        likelihood=4,
        impact=4,
        mitigation_ids=("mit.social_engineering_detector", "mit.policy_overlay", "mit.incident_playbooks", "mit.audit_chain"),
        detection_signals=(
            "authority_impersonation",
            "urgency_pressure",
            "credential_harvest",
            "policy_bypass_request",
        ),
    )

    return model


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ThreatModelError(f"{field_name} is required")
    return normalized


def _normalize_tuple(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    normalized = sorted({_normalize_required(str(value), "value") for value in values})
    return tuple(normalized)


def _normalize_score(value: int, field_name: str) -> int:
    if value < 1 or value > 5:
        raise ThreatModelError(f"{field_name} must be between 1 and 5")
    return int(value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "ThreatMitigation",
    "ThreatAbuseCase",
    "ThreatCaseMapping",
    "ThreatModelReport",
    "ThreatModelError",
    "ThreatModelRegistry",
    "build_default_threat_model",
]
