"""Bounded autonomy policy for low, medium, high, and critical task tiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["low", "medium", "high", "critical"]
ApprovalRoute = Literal["auto_approve", "requires_supervisor", "escalate_human", "deny"]


@dataclass(frozen=True)
class AutonomyPolicyRule:
    risk_level: RiskLevel
    allowed_routes: tuple[ApprovalRoute, ...]
    required_controls: tuple[str, ...]
    autonomous_mode: str


@dataclass(frozen=True)
class AutonomyPolicyDecision:
    action_id: str
    risk_level: RiskLevel
    route: ApprovalRoute
    allowed: bool
    mode: str
    required_controls: tuple[str, ...]
    reason: str


class AutonomyPolicyError(ValueError):
    """Raised when autonomy policy evaluation receives invalid inputs."""


class BoundedAutonomyPolicy:
    """Enforces risk-tier autonomy bounds before action execution."""

    def __init__(self, rules: dict[RiskLevel, AutonomyPolicyRule] | None = None) -> None:
        self._rules = rules or _default_rules()

    def evaluate(
        self,
        *,
        action_id: str,
        risk_level: RiskLevel | str,
        route: ApprovalRoute | str,
        is_destructive: bool = False,
    ) -> AutonomyPolicyDecision:
        normalized_action_id = _normalize_required(action_id, "action_id")
        normalized_risk = _normalize_risk(risk_level)
        normalized_route = _normalize_route(route)

        rule = self._rules[normalized_risk]
        allowed = normalized_route in rule.allowed_routes
        controls = list(rule.required_controls)

        if is_destructive and normalized_risk in {"medium", "high", "critical"}:
            if "dry_run_required" not in controls:
                controls.append("dry_run_required")

        if normalized_route == "requires_supervisor" and "supervisor_ack_required" not in controls:
            controls.append("supervisor_ack_required")
        if normalized_route == "escalate_human" and "human_approval_required" not in controls:
            controls.append("human_approval_required")

        if not allowed:
            reason = (
                f"Route {normalized_route} is outside autonomy bounds for risk tier {normalized_risk}."
            )
            return AutonomyPolicyDecision(
                action_id=normalized_action_id,
                risk_level=normalized_risk,
                route=normalized_route,
                allowed=False,
                mode="blocked",
                required_controls=tuple(sorted(set(controls))),
                reason=reason,
            )

        mode = rule.autonomous_mode
        if normalized_route == "requires_supervisor":
            mode = "supervised"
        elif normalized_route == "escalate_human":
            mode = "manual"

        return AutonomyPolicyDecision(
            action_id=normalized_action_id,
            risk_level=normalized_risk,
            route=normalized_route,
            allowed=True,
            mode=mode,
            required_controls=tuple(sorted(set(controls))),
            reason=(
                f"Route {normalized_route} is permitted for risk tier {normalized_risk} "
                f"in {mode} mode."
            ),
        )


def _default_rules() -> dict[RiskLevel, AutonomyPolicyRule]:
    return {
        "low": AutonomyPolicyRule(
            risk_level="low",
            allowed_routes=("auto_approve", "requires_supervisor"),
            required_controls=(),
            autonomous_mode="autonomous",
        ),
        "medium": AutonomyPolicyRule(
            risk_level="medium",
            allowed_routes=("auto_approve", "requires_supervisor", "escalate_human"),
            required_controls=("policy_trace_required",),
            autonomous_mode="autonomous",
        ),
        "high": AutonomyPolicyRule(
            risk_level="high",
            allowed_routes=("requires_supervisor", "escalate_human"),
            required_controls=("policy_trace_required", "risk_ack_required"),
            autonomous_mode="supervised",
        ),
        "critical": AutonomyPolicyRule(
            risk_level="critical",
            allowed_routes=("escalate_human",),
            required_controls=(
                "policy_trace_required",
                "risk_ack_required",
                "human_approval_required",
            ),
            autonomous_mode="manual",
        ),
    }


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise AutonomyPolicyError(f"{field_name} is required")
    return normalized


def _normalize_risk(value: RiskLevel | str) -> RiskLevel:
    normalized = _normalize_required(str(value), "risk_level").lower()
    if normalized not in {"low", "medium", "high", "critical"}:
        raise AutonomyPolicyError(f"Unsupported risk_level: {value}")
    return normalized  # type: ignore[return-value]


def _normalize_route(value: ApprovalRoute | str) -> ApprovalRoute:
    normalized = _normalize_required(str(value), "route").lower()
    if normalized not in {"auto_approve", "requires_supervisor", "escalate_human", "deny"}:
        raise AutonomyPolicyError(f"Unsupported route: {value}")
    return normalized  # type: ignore[return-value]


__all__ = [
    "ApprovalRoute",
    "AutonomyPolicyDecision",
    "AutonomyPolicyError",
    "AutonomyPolicyRule",
    "BoundedAutonomyPolicy",
    "RiskLevel",
]
