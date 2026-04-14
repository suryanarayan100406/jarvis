"""Policy anomaly detector for suspicious command patterns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["low", "medium", "high", "critical"]
PolicyDecision = Literal["allow", "deny", "require_approval"]


@dataclass(frozen=True)
class CommandPolicyEvent:
    operator_id: str
    operation: str
    command: str
    decision: PolicyDecision
    host_role: str = "unknown"


@dataclass(frozen=True)
class PolicyAnomalySignal:
    signal: str
    score: float
    detail: str


@dataclass(frozen=True)
class PolicyAnomalyAssessment:
    anomaly_score: float
    risk_level: RiskLevel
    should_escalate: bool
    recommended_action: str
    command_signature: str
    signals: tuple[PolicyAnomalySignal, ...]


@dataclass(frozen=True)
class _NormalizedEvent:
    operator_id: str
    operation: str
    command: str
    decision: PolicyDecision
    host_role: str
    signature: str


class PolicyAnomalyDetector:
    """Tracks command-policy events and flags suspicious policy-behavior anomalies."""

    _dangerous_tokens = (
        "&&",
        "||",
        "rm -rf",
        "mkfs",
        "format",
        "drop database",
        "truncate table",
        "invoke-expression",
        "iex ",
    )
    _privilege_patterns = (
        re.compile(r"\bsudo\b", re.IGNORECASE),
        re.compile(r"\bsu\b", re.IGNORECASE),
        re.compile(r"\bset-executionpolicy\b.{0,20}\bbypass\b", re.IGNORECASE),
        re.compile(r"\bchmod\b.{0,20}\b777\b", re.IGNORECASE),
        re.compile(r"\bnet user\b.{0,30}\b/add\b", re.IGNORECASE),
        re.compile(r"\b(add-admin|grant-admin|admin role)\b", re.IGNORECASE),
    )

    def __init__(self, *, max_history_per_operator: int = 100, deny_burst_window: int = 5, deny_burst_threshold: int = 3) -> None:
        if max_history_per_operator < 3:
            raise ValueError("max_history_per_operator must be at least 3")
        if deny_burst_window < 2:
            raise ValueError("deny_burst_window must be at least 2")
        if deny_burst_threshold < 2:
            raise ValueError("deny_burst_threshold must be at least 2")
        if deny_burst_threshold > deny_burst_window:
            raise ValueError("deny_burst_threshold cannot exceed deny_burst_window")

        self.max_history_per_operator = max_history_per_operator
        self.deny_burst_window = deny_burst_window
        self.deny_burst_threshold = deny_burst_threshold

        self._recent_events: dict[str, list[_NormalizedEvent]] = {}
        self._allowed_signatures: dict[str, set[str]] = {}

    def register_baseline(self, *, operator_id: str, operation: str, command: str) -> str:
        """Seed a known-good command signature for an operator."""
        normalized_operator = _normalize_required(operator_id, "operator_id").lower()
        signature = _build_signature(operation, command)
        self._allowed_signatures.setdefault(normalized_operator, set()).add(signature)
        return signature

    def analyze_event(self, event: CommandPolicyEvent) -> PolicyAnomalyAssessment:
        """Analyze one command-policy event and update detector history state."""
        normalized = self._normalize_event(event)
        operator_history = self._recent_events.setdefault(normalized.operator_id, [])
        known_signatures = self._allowed_signatures.setdefault(normalized.operator_id, set())

        signals: list[PolicyAnomalySignal] = []

        if any(token in normalized.command for token in self._dangerous_tokens):
            signals.append(
                PolicyAnomalySignal(
                    signal="dangerous_token_detected",
                    score=0.36,
                    detail="Command includes high-risk token associated with policy bypass or destructive behavior.",
                )
            )

        if any(pattern.search(normalized.command) for pattern in self._privilege_patterns):
            signals.append(
                PolicyAnomalySignal(
                    signal="privilege_escalation_pattern",
                    score=0.30,
                    detail="Command matches privilege escalation or admin-control pattern.",
                )
            )

        if normalized.signature not in known_signatures:
            signals.append(
                PolicyAnomalySignal(
                    signal="novel_command_pattern",
                    score=0.20,
                    detail="Command signature is new for this operator and requires scrutiny.",
                )
            )

        deny_count = self._projected_deny_count(operator_history, normalized)
        if deny_count >= self.deny_burst_threshold:
            signals.append(
                PolicyAnomalySignal(
                    signal="deny_burst_pattern",
                    score=0.24,
                    detail=f"Detected {deny_count} denies in the last {self.deny_burst_window} events.",
                )
            )

        anomaly_score = min(1.0, sum(signal.score for signal in signals))
        risk_level = _risk_level_for_score(anomaly_score)

        severe_combo = _has_signals(signals, "dangerous_token_detected", "privilege_escalation_pattern")
        burst_combo = _has_signals(signals, "deny_burst_pattern", "dangerous_token_detected")
        should_escalate = risk_level in {"high", "critical"} or severe_combo or burst_combo
        recommended_action = _recommended_action(should_escalate, risk_level)

        if normalized.decision == "allow":
            known_signatures.add(normalized.signature)

        operator_history.append(normalized)
        self._trim_history(operator_history)

        ordered_signals = tuple(sorted(signals, key=lambda item: item.score, reverse=True))
        return PolicyAnomalyAssessment(
            anomaly_score=round(anomaly_score, 4),
            risk_level=risk_level,
            should_escalate=should_escalate,
            recommended_action=recommended_action,
            command_signature=normalized.signature,
            signals=ordered_signals,
        )

    def _normalize_event(self, event: CommandPolicyEvent) -> _NormalizedEvent:
        decision = _normalize_required(event.decision, "decision").lower()
        if decision not in {"allow", "deny", "require_approval"}:
            raise ValueError(f"Unsupported decision: {event.decision}")

        operator_id = _normalize_required(event.operator_id, "operator_id").lower()
        operation = _normalize_required(event.operation, "operation").lower()
        command = _normalize_command(event.command)
        host_role = _normalize_required(event.host_role, "host_role").lower()
        signature = _build_signature(operation, command)

        return _NormalizedEvent(
            operator_id=operator_id,
            operation=operation,
            command=command,
            decision=decision,
            host_role=host_role,
            signature=signature,
        )

    def _projected_deny_count(self, history: list[_NormalizedEvent], current: _NormalizedEvent) -> int:
        window_events = history[-(self.deny_burst_window - 1) :] if self.deny_burst_window > 1 else []
        projected = list(window_events) + [current]
        return sum(1 for item in projected if item.decision == "deny")

    def _trim_history(self, history: list[_NormalizedEvent]) -> None:
        overflow = len(history) - self.max_history_per_operator
        if overflow > 0:
            del history[:overflow]


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalize_command(command: str) -> str:
    normalized = _normalize_required(command, "command").lower()
    if any(char in normalized for char in ("\n", "\r", "\x00")):
        raise ValueError("command contains disallowed control characters")
    return normalized


def _build_signature(operation: str, command: str) -> str:
    normalized_operation = _normalize_required(operation, "operation").lower()
    normalized_command = _normalize_command(command)
    command_shape = re.sub(r"\d+", "<num>", normalized_command)
    command_shape = re.sub(r"\s+", " ", command_shape)
    return f"{normalized_operation}::{command_shape}"


def _risk_level_for_score(score: float) -> RiskLevel:
    if score >= 0.75:
        return "critical"
    if score >= 0.5:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"


def _has_signals(signals: list[PolicyAnomalySignal], first: str, second: str) -> bool:
    names = {item.signal for item in signals}
    return first in names and second in names


def _recommended_action(should_escalate: bool, risk_level: RiskLevel) -> str:
    if should_escalate:
        return "escalate_to_supervisor"
    if risk_level == "medium":
        return "require_additional_review"
    return "monitor"


__all__ = [
    "CommandPolicyEvent",
    "PolicyAnomalyAssessment",
    "PolicyAnomalyDetector",
    "PolicyAnomalySignal",
]
