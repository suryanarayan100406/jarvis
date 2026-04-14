"""Policy overlay for host, command, and operator scope in control-plane execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from runtime.policy import PolicyDecisionResult, PolicyEngine, PolicyRequest

from .host_inventory import HostInventoryService, HostRecord

Decision = Literal["allow", "deny", "require_approval"]


@dataclass(frozen=True)
class ControlPlanePolicyRequest:
    operator_id: str
    operator_role: str
    host: HostRecord
    operation: str
    command: str
    dry_run: bool = False
    environment: str = "unknown"


@dataclass(frozen=True)
class ControlPlanePolicyDecision:
    decision: Decision
    risk_tier: str
    rule_id: str
    reason: str
    base_rule_id: str
    base_decision: Decision


@dataclass(frozen=True)
class HostScopePolicy:
    host_id: str
    allowed_operators: tuple[str, ...] | None
    allowed_operations: tuple[str, ...] | None
    require_approval_operations: tuple[str, ...]


@dataclass(frozen=True)
class OperatorScopePolicy:
    subject: str
    allowed_host_roles: tuple[str, ...] | None
    allowed_host_ids: tuple[str, ...] | None
    denied_operations: tuple[str, ...]
    require_approval_operations: tuple[str, ...]


@dataclass(frozen=True)
class CommandScopePolicy:
    operation: str
    allowed_prefixes: tuple[str, ...] | None
    blocked_substrings: tuple[str, ...]
    require_approval: bool


class ControlPlanePolicyOverlayError(ValueError):
    """Raised when policy overlay configuration or evaluation is invalid."""


class ControlPlanePolicyOverlay:
    """Composes scoped policy checks with base risk-tier policy decisions."""

    def __init__(self, policy_engine: PolicyEngine | None = None) -> None:
        self.policy_engine = policy_engine or PolicyEngine()
        self._host_policies: dict[str, HostScopePolicy] = {}
        self._operator_policies: dict[str, OperatorScopePolicy] = {}
        self._role_policies: dict[str, OperatorScopePolicy] = {}
        self._command_policies: dict[str, CommandScopePolicy] = {}
        self._global_blocked_tokens: tuple[str, ...] = ("&&", "||", ";", "`", "$(")

    def set_host_policy(
        self,
        *,
        host_id: str,
        allowed_operators: list[str] | tuple[str, ...] | None = None,
        allowed_operations: list[str] | tuple[str, ...] | None = None,
        require_approval_operations: list[str] | tuple[str, ...] | None = None,
    ) -> HostScopePolicy:
        normalized_host_id = _normalize_required(host_id, "host_id")

        policy = HostScopePolicy(
            host_id=normalized_host_id,
            allowed_operators=_normalize_sequence(allowed_operators),
            allowed_operations=_normalize_operations(allowed_operations),
            require_approval_operations=_normalize_operations(require_approval_operations) or (),
        )
        self._host_policies[normalized_host_id] = policy
        return policy

    def set_operator_policy(
        self,
        *,
        operator_id: str,
        allowed_host_roles: list[str] | tuple[str, ...] | None = None,
        allowed_host_ids: list[str] | tuple[str, ...] | None = None,
        denied_operations: list[str] | tuple[str, ...] | None = None,
        require_approval_operations: list[str] | tuple[str, ...] | None = None,
    ) -> OperatorScopePolicy:
        normalized_operator_id = _normalize_required(operator_id, "operator_id")

        policy = OperatorScopePolicy(
            subject=normalized_operator_id,
            allowed_host_roles=self._normalize_host_roles(allowed_host_roles),
            allowed_host_ids=_normalize_sequence(allowed_host_ids),
            denied_operations=_normalize_operations(denied_operations) or (),
            require_approval_operations=_normalize_operations(require_approval_operations) or (),
        )
        self._operator_policies[normalized_operator_id] = policy
        return policy

    def set_role_policy(
        self,
        *,
        operator_role: str,
        allowed_host_roles: list[str] | tuple[str, ...] | None = None,
        allowed_host_ids: list[str] | tuple[str, ...] | None = None,
        denied_operations: list[str] | tuple[str, ...] | None = None,
        require_approval_operations: list[str] | tuple[str, ...] | None = None,
    ) -> OperatorScopePolicy:
        normalized_role = _normalize_required(operator_role, "operator_role")

        policy = OperatorScopePolicy(
            subject=normalized_role,
            allowed_host_roles=self._normalize_host_roles(allowed_host_roles),
            allowed_host_ids=_normalize_sequence(allowed_host_ids),
            denied_operations=_normalize_operations(denied_operations) or (),
            require_approval_operations=_normalize_operations(require_approval_operations) or (),
        )
        self._role_policies[normalized_role] = policy
        return policy

    def set_command_policy(
        self,
        *,
        operation: str,
        allowed_prefixes: list[str] | tuple[str, ...] | None = None,
        blocked_substrings: list[str] | tuple[str, ...] | None = None,
        require_approval: bool = False,
    ) -> CommandScopePolicy:
        normalized_operation = _normalize_required(operation, "operation").lower()
        normalized_allowed_prefixes = _normalize_sequence(allowed_prefixes)
        normalized_blocked_substrings = _normalize_sequence(blocked_substrings) or ()

        policy = CommandScopePolicy(
            operation=normalized_operation,
            allowed_prefixes=normalized_allowed_prefixes,
            blocked_substrings=normalized_blocked_substrings,
            require_approval=require_approval,
        )
        self._command_policies[normalized_operation] = policy
        return policy

    def evaluate(self, request: ControlPlanePolicyRequest) -> ControlPlanePolicyDecision:
        normalized = self._normalize_request(request)
        base = self._evaluate_base_policy(normalized)
        if base.decision == "deny":
            return self._decision(
                decision="deny",
                rule_id=base.rule_id,
                reason=base.reason,
                base=base,
            )

        command_lower = normalized.command.lower()
        for token in self._global_blocked_tokens:
            if token in command_lower:
                return self._decision(
                    decision="deny",
                    rule_id="overlay.command.global_token.deny",
                    reason=f"Command contains blocked token: {token}",
                    base=base,
                )

        decision: Decision = base.decision
        pending_rule_id = base.rule_id
        pending_reason = base.reason

        host_policy = self._host_policies.get(normalized.host.host_id)
        if host_policy is not None:
            host_result = self._evaluate_host_scope(normalized, host_policy)
            if host_result is not None:
                if host_result[0] == "deny":
                    return self._decision(host_result[0], host_result[1], host_result[2], base)
                decision = _escalate_decision(decision, host_result[0])
                pending_rule_id = host_result[1]
                pending_reason = host_result[2]

        operator_policy = self._operator_policies.get(normalized.operator_id)
        if operator_policy is not None:
            operator_result = self._evaluate_operator_scope(normalized, operator_policy, "operator")
            if operator_result is not None:
                if operator_result[0] == "deny":
                    return self._decision(operator_result[0], operator_result[1], operator_result[2], base)
                decision = _escalate_decision(decision, operator_result[0])
                pending_rule_id = operator_result[1]
                pending_reason = operator_result[2]

        role_policy = self._role_policies.get(normalized.operator_role)
        if role_policy is not None:
            role_result = self._evaluate_operator_scope(normalized, role_policy, "role")
            if role_result is not None:
                if role_result[0] == "deny":
                    return self._decision(role_result[0], role_result[1], role_result[2], base)
                decision = _escalate_decision(decision, role_result[0])
                pending_rule_id = role_result[1]
                pending_reason = role_result[2]

        command_policy = self._command_policies.get(normalized.operation)
        if command_policy is not None:
            command_result = self._evaluate_command_scope(normalized, command_policy)
            if command_result is not None:
                if command_result[0] == "deny":
                    return self._decision(command_result[0], command_result[1], command_result[2], base)
                decision = _escalate_decision(decision, command_result[0])
                pending_rule_id = command_result[1]
                pending_reason = command_result[2]

        return self._decision(
            decision=decision,
            rule_id=pending_rule_id,
            reason=pending_reason,
            base=base,
        )

    def _normalize_request(self, request: ControlPlanePolicyRequest) -> ControlPlanePolicyRequest:
        normalized_operator_id = _normalize_required(request.operator_id, "operator_id")
        normalized_operator_role = _normalize_required(request.operator_role, "operator_role")
        normalized_operation = _normalize_required(request.operation, "operation").lower()

        if not isinstance(request.command, str):
            raise ControlPlanePolicyOverlayError("command must be a string")
        if any(char in request.command for char in ("\n", "\r", "\x00")):
            raise ControlPlanePolicyOverlayError("command contains disallowed control characters")

        normalized_command = _normalize_required(request.command, "command")

        return ControlPlanePolicyRequest(
            operator_id=normalized_operator_id,
            operator_role=normalized_operator_role,
            host=request.host,
            operation=normalized_operation,
            command=normalized_command,
            dry_run=request.dry_run,
            environment=_normalize_required(request.environment, "environment").lower(),
        )

    def _evaluate_base_policy(self, request: ControlPlanePolicyRequest) -> PolicyDecisionResult:
        return self.policy_engine.evaluate(
            PolicyRequest(
                actor_role=request.operator_role,
                tool_name="control_plane",
                tool_action=request.operation,
                target_scope="host",
                environment=request.environment,
                dry_run=request.dry_run,
            )
        )

    @staticmethod
    def _evaluate_host_scope(
        request: ControlPlanePolicyRequest,
        policy: HostScopePolicy,
    ) -> tuple[Decision, str, str] | None:
        if policy.allowed_operators is not None and request.operator_id not in policy.allowed_operators:
            return (
                "deny",
                "overlay.host.operator.deny",
                f"Operator {request.operator_id} is not allowed for host {request.host.hostname}",
            )

        if policy.allowed_operations is not None and request.operation not in policy.allowed_operations:
            return (
                "deny",
                "overlay.host.operation.deny",
                f"Operation {request.operation} is not allowed for host {request.host.hostname}",
            )

        if request.operation in policy.require_approval_operations:
            return (
                "require_approval",
                "overlay.host.operation.require_approval",
                f"Operation {request.operation} requires approval on host {request.host.hostname}",
            )

        return None

    def _evaluate_operator_scope(
        self,
        request: ControlPlanePolicyRequest,
        policy: OperatorScopePolicy,
        scope_type: str,
    ) -> tuple[Decision, str, str] | None:
        if policy.allowed_host_roles is not None and request.host.role not in policy.allowed_host_roles:
            return (
                "deny",
                f"overlay.{scope_type}.host_role.deny",
                f"Host role {request.host.role} is not allowed for {scope_type} scope {policy.subject}",
            )

        if policy.allowed_host_ids is not None and request.host.host_id not in policy.allowed_host_ids:
            return (
                "deny",
                f"overlay.{scope_type}.host_id.deny",
                f"Host {request.host.hostname} is not allowed for {scope_type} scope {policy.subject}",
            )

        if request.operation in policy.denied_operations:
            return (
                "deny",
                f"overlay.{scope_type}.operation.deny",
                f"Operation {request.operation} is denied for {scope_type} scope {policy.subject}",
            )

        if request.operation in policy.require_approval_operations:
            return (
                "require_approval",
                f"overlay.{scope_type}.operation.require_approval",
                f"Operation {request.operation} requires approval for {scope_type} scope {policy.subject}",
            )

        return None

    @staticmethod
    def _evaluate_command_scope(
        request: ControlPlanePolicyRequest,
        policy: CommandScopePolicy,
    ) -> tuple[Decision, str, str] | None:
        command_lower = request.command.lower()

        for blocked in policy.blocked_substrings:
            if blocked.lower() in command_lower:
                return (
                    "deny",
                    "overlay.command.substring.deny",
                    f"Command contains blocked substring: {blocked}",
                )

        if policy.allowed_prefixes is not None and not any(
            command_lower.startswith(prefix.lower()) for prefix in policy.allowed_prefixes
        ):
            prefixes = ", ".join(policy.allowed_prefixes)
            return (
                "deny",
                "overlay.command.prefix.deny",
                f"Command does not match allowed prefixes for {request.operation}: {prefixes}",
            )

        if policy.require_approval:
            return (
                "require_approval",
                "overlay.command.require_approval",
                f"Command for operation {request.operation} requires approval by overlay policy",
            )

        return None

    def _normalize_host_roles(self, roles: list[str] | tuple[str, ...] | None) -> tuple[str, ...] | None:
        if roles is None:
            return None

        normalized = sorted({_normalize_required(role, "host_role").lower() for role in roles})
        unsupported = [role for role in normalized if role not in HostInventoryService.allowed_roles]
        if unsupported:
            allowed = ", ".join(sorted(HostInventoryService.allowed_roles))
            raise ControlPlanePolicyOverlayError(
                f"Unsupported host roles in policy scope: {', '.join(unsupported)}. Allowed: {allowed}"
            )
        return tuple(normalized)

    @staticmethod
    def _decision(
        decision: Decision,
        rule_id: str,
        reason: str,
        base: PolicyDecisionResult,
    ) -> ControlPlanePolicyDecision:
        return ControlPlanePolicyDecision(
            decision=decision,
            risk_tier=base.risk_tier,
            rule_id=rule_id,
            reason=reason,
            base_rule_id=base.rule_id,
            base_decision=base.decision,
        )


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ControlPlanePolicyOverlayError(f"{field_name} is required")
    return normalized


def _normalize_sequence(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    normalized = sorted({_normalize_required(value, "scope_value") for value in values})
    return tuple(normalized) if normalized else None


def _normalize_operations(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    normalized = sorted({_normalize_required(value, "operation_scope").lower() for value in values})
    return tuple(normalized) if normalized else None


def _escalate_decision(current: Decision, requested: Decision) -> Decision:
    if current == "deny" or requested == "deny":
        return "deny"
    if current == "require_approval" or requested == "require_approval":
        return "require_approval"
    return "allow"
