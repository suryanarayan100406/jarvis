"""Risk-tier policy engine for FRIDAY runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

RiskTier = Literal["low", "medium", "high", "critical"]
Decision = Literal["allow", "deny", "require_approval"]
Role = Literal["primary_user", "authorized_operator", "limited_user", "system"]

_RISK_ORDER: dict[RiskTier, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


@dataclass(frozen=True)
class PolicyRequest:
    """Normalized policy request context used by the policy engine."""

    actor_role: str
    tool_name: str
    tool_action: str
    target_scope: str
    environment: str = "unknown"
    dry_run: bool = False
    declared_risk_tier: RiskTier | None = None


@dataclass(frozen=True)
class PolicyDecisionResult:
    """Policy decision output format aligned with contract expectations."""

    decision: Decision
    risk_tier: RiskTier
    rule_id: str
    reason: str
    evaluated_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "decision": self.decision,
            "risk_tier": self.risk_tier,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "evaluated_at": self.evaluated_at,
        }


class PolicyEngine:
    """Evaluates policy requests using risk classification and role-based rules."""

    def evaluate(self, request: PolicyRequest | dict[str, Any]) -> PolicyDecisionResult:
        normalized = self._normalize_request(request)
        risk = self._evaluate_risk_tier(normalized)
        evaluated_at = _utc_now_iso()

        if normalized.actor_role not in {"primary_user", "authorized_operator", "limited_user", "system"}:
            return PolicyDecisionResult(
                decision="deny",
                risk_tier=risk,
                rule_id="policy.actor.unknown.deny",
                reason="Unrecognized actor role.",
                evaluated_at=evaluated_at,
            )

        if normalized.actor_role == "limited_user" and risk in {"high", "critical"}:
            return PolicyDecisionResult(
                decision="deny",
                risk_tier=risk,
                rule_id="policy.limited.high_critical.deny",
                reason="Limited users cannot execute high or critical risk actions.",
                evaluated_at=evaluated_at,
            )

        if risk == "critical":
            return PolicyDecisionResult(
                decision="require_approval",
                risk_tier=risk,
                rule_id="policy.critical.require_approval",
                reason="Critical-risk action requires explicit approval.",
                evaluated_at=evaluated_at,
            )

        if risk == "high":
            return PolicyDecisionResult(
                decision="require_approval",
                risk_tier=risk,
                rule_id="policy.high.require_approval",
                reason="High-risk action requires explicit approval.",
                evaluated_at=evaluated_at,
            )

        if risk == "medium" and normalized.actor_role == "limited_user":
            return PolicyDecisionResult(
                decision="require_approval",
                risk_tier=risk,
                rule_id="policy.limited.medium.require_approval",
                reason="Limited users require approval for medium-risk actions.",
                evaluated_at=evaluated_at,
            )

        return PolicyDecisionResult(
            decision="allow",
            risk_tier=risk,
            rule_id="policy.default.allow",
            reason="Action is within allowed risk and role boundaries.",
            evaluated_at=evaluated_at,
        )

    def _normalize_request(self, request: PolicyRequest | dict[str, Any]) -> PolicyRequest:
        if isinstance(request, PolicyRequest):
            return request

        if not isinstance(request, dict):
            raise TypeError("Policy request must be a PolicyRequest or dict")

        actor = request.get("actor", {})
        tool = request.get("tool", {})
        target = request.get("target", {})
        execution = request.get("execution", {})
        policy_context = request.get("policy_context", {})

        return PolicyRequest(
            actor_role=str(actor.get("role", "")),
            tool_name=str(tool.get("name", "")),
            tool_action=str(tool.get("action", "")),
            target_scope=str(target.get("scope", "")),
            environment=str(target.get("environment", "unknown")),
            dry_run=bool(execution.get("dry_run", False)),
            declared_risk_tier=_parse_risk_tier(policy_context.get("risk_tier")),
        )

    def _evaluate_risk_tier(self, request: PolicyRequest) -> RiskTier:
        risk: RiskTier = "low"
        action = request.tool_action.lower()
        scope = request.target_scope.lower()
        tool_name = request.tool_name.lower()

        if scope in {"host", "service", "network", "ui"}:
            risk = _max_risk(risk, "medium")

        if scope == "physical":
            risk = "critical"

        medium_keywords = {
            "write",
            "create",
            "update",
            "modify",
            "install",
            "deploy",
            "configure",
        }
        high_keywords = {
            "stop",
            "terminate",
            "shutdown",
            "restart",
            "reboot",
            "delete",
            "drop",
            "disable",
            "kill",
            "wipe",
            "format",
        }
        critical_keywords = {
            "unlock",
            "disarm",
            "poweroff",
            "factory_reset",
        }

        if _contains_keyword(action, medium_keywords):
            risk = _max_risk(risk, "medium")

        if _contains_keyword(action, high_keywords):
            risk = _max_risk(risk, "high")

        if _contains_keyword(action, critical_keywords):
            risk = "critical"

        if tool_name in {"security", "secrets", "credential_manager"} and "rotate" in action:
            risk = _max_risk(risk, "high")

        if request.environment == "prod" and risk in {"medium", "high"}:
            risk = _promote_risk(risk)

        if request.dry_run and risk in {"high", "critical"}:
            risk = _demote_risk(risk)

        if request.declared_risk_tier is not None:
            risk = _max_risk(risk, request.declared_risk_tier)

        return risk


def _parse_risk_tier(value: Any) -> RiskTier | None:
    if value is None:
        return None
    if value in {"low", "medium", "high", "critical"}:
        return value
    return None


def _contains_keyword(action: str, keywords: set[str]) -> bool:
    return any(keyword in action for keyword in keywords)


def _max_risk(left: RiskTier, right: RiskTier) -> RiskTier:
    return left if _RISK_ORDER[left] >= _RISK_ORDER[right] else right


def _promote_risk(risk: RiskTier) -> RiskTier:
    if risk == "medium":
        return "high"
    if risk == "high":
        return "critical"
    return risk


def _demote_risk(risk: RiskTier) -> RiskTier:
    if risk == "critical":
        return "high"
    if risk == "high":
        return "medium"
    return risk


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
