"""Confidence model for action approval routing in autonomous operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["low", "medium", "high", "critical"]
ApprovalRoute = Literal["auto_approve", "requires_supervisor", "escalate_human", "deny"]

_RISK_PENALTY: dict[str, float] = {
    "low": 0.00,
    "medium": 0.08,
    "high": 0.18,
    "critical": 0.30,
}


@dataclass(frozen=True)
class ActionConfidenceInput:
    action_id: str
    action_type: str
    risk_level: RiskLevel
    evidence_score: float
    policy_compliance_score: float
    execution_reliability_score: float
    blast_radius: int = 0


@dataclass(frozen=True)
class ApprovalRoutingDecision:
    action_id: str
    confidence_score: float
    confidence_band: str
    route: ApprovalRoute
    rationale: tuple[str, ...]


class ConfidenceRoutingError(ValueError):
    """Raised when confidence routing receives invalid inputs."""


class ActionApprovalConfidenceModel:
    """Calculates confidence and routes action approval by risk tier."""

    def assess(self, request: ActionConfidenceInput) -> ApprovalRoutingDecision:
        normalized_action_id = _normalize_required(request.action_id, "action_id")
        _normalize_required(request.action_type, "action_type")
        risk_level = _normalize_risk(request.risk_level)

        evidence = _normalize_score(request.evidence_score, "evidence_score")
        policy = _normalize_score(request.policy_compliance_score, "policy_compliance_score")
        reliability = _normalize_score(request.execution_reliability_score, "execution_reliability_score")
        blast_radius = _normalize_blast_radius(request.blast_radius)

        weighted = (evidence * 0.35) + (policy * 0.35) + (reliability * 0.30)
        risk_penalty = _RISK_PENALTY[risk_level]
        blast_penalty = min(0.20, blast_radius * 0.02)
        score = _clamp(round(weighted - risk_penalty - blast_penalty, 6), minimum=0.0, maximum=1.0)

        confidence_band = _band(score)
        route = _route_decision(risk_level=risk_level, score=score)

        rationale = (
            f"risk={risk_level}",
            f"weighted={weighted:.3f}",
            f"risk_penalty={risk_penalty:.3f}",
            f"blast_penalty={blast_penalty:.3f}",
            f"band={confidence_band}",
        )

        return ApprovalRoutingDecision(
            action_id=normalized_action_id,
            confidence_score=score,
            confidence_band=confidence_band,
            route=route,
            rationale=rationale,
        )


def _route_decision(*, risk_level: RiskLevel, score: float) -> ApprovalRoute:
    if risk_level == "critical":
        if score >= 0.90:
            return "requires_supervisor"
        return "escalate_human"

    if risk_level == "high":
        if score >= 0.80:
            return "requires_supervisor"
        if score >= 0.60:
            return "escalate_human"
        return "deny"

    if risk_level == "medium":
        if score >= 0.75:
            return "auto_approve"
        if score >= 0.55:
            return "requires_supervisor"
        return "escalate_human"

    if score >= 0.60:
        return "auto_approve"
    return "requires_supervisor"


def _band(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.50:
        return "medium"
    return "low"


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ConfidenceRoutingError(f"{field_name} is required")
    return normalized


def _normalize_risk(value: RiskLevel | str) -> RiskLevel:
    normalized = _normalize_required(str(value), "risk_level").lower()
    if normalized not in _RISK_PENALTY:
        allowed = ", ".join(_RISK_PENALTY.keys())
        raise ConfidenceRoutingError(f"Unsupported risk_level: {value}. Allowed: {allowed}")
    return normalized  # type: ignore[return-value]


def _normalize_score(value: float, field_name: str) -> float:
    if value < 0 or value > 1:
        raise ConfidenceRoutingError(f"{field_name} must be between 0 and 1")
    return float(value)


def _normalize_blast_radius(value: int) -> int:
    if value < 0:
        raise ConfidenceRoutingError("blast_radius must be non-negative")
    return int(value)


def _clamp(value: float, *, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


__all__ = [
    "ActionApprovalConfidenceModel",
    "ActionConfidenceInput",
    "ApprovalRoute",
    "ApprovalRoutingDecision",
    "ConfidenceRoutingError",
    "RiskLevel",
]
