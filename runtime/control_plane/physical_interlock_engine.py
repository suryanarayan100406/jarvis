"""Safety interlock engine for physical command authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .physical_device_registry import PhysicalDeviceRegistry

Decision = Literal["allow", "deny", "require_approval"]
ExecutionMode = Literal["simulation", "live"]

_TRUST_RANK: dict[str, int] = {
    "untrusted": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


@dataclass(frozen=True)
class PhysicalInterlockRequest:
    device_id: str
    capability_id: str
    execution_mode: ExecutionMode
    operator_role: str
    sandbox_approved: bool = False


@dataclass(frozen=True)
class PhysicalInterlockDecision:
    decision: Decision
    rule_id: str
    reason: str
    required_controls: tuple[str, ...]
    device_id: str
    capability_id: str
    execution_mode: ExecutionMode


class PhysicalInterlockError(ValueError):
    """Raised when physical interlock inputs are invalid."""


class PhysicalSafetyInterlockEngine:
    """Evaluates interlocks that gate physical command execution."""

    _live_operator_allowlist = {"primary_user", "authorized_operator"}

    def __init__(self, device_registry: PhysicalDeviceRegistry) -> None:
        self.device_registry = device_registry

    def evaluate(self, request: PhysicalInterlockRequest) -> PhysicalInterlockDecision:
        normalized = self._normalize_request(request)

        try:
            device = self.device_registry.get_device(normalized.device_id)
            capability = self.device_registry.get_capability_profile(
                normalized.device_id,
                normalized.capability_id,
            )
        except KeyError as exc:
            raise PhysicalInterlockError(f"Unknown physical device: {normalized.device_id}") from exc
        except Exception as exc:
            raise PhysicalInterlockError(str(exc)) from exc

        if not device.enabled:
            return self._decision(
                normalized,
                decision="deny",
                rule_id="interlock.device.disabled.deny",
                reason=f"Device {device.device_id} is disabled",
            )

        controls: list[str] = []
        if normalized.execution_mode == "live":
            controls.extend(["simulation_required", "policy_authorization_required"])

        if normalized.execution_mode == "simulation" and not capability.simulation_supported:
            return self._decision(
                normalized,
                decision="deny",
                rule_id="interlock.capability.simulation_unsupported.deny",
                reason=(
                    f"Capability {capability.capability_id} does not support simulation mode"
                ),
                required_controls=controls,
            )

        if normalized.execution_mode == "live" and capability.capability_type in {"actuator", "hybrid"}:
            controls.append("operator_presence_required")
            if not normalized.sandbox_approved:
                return self._decision(
                    normalized,
                    decision="deny",
                    rule_id="interlock.live.sandbox_required.deny",
                    reason=(
                        f"Live {capability.capability_type} command requires sandbox approval"
                    ),
                    required_controls=controls + ["sandbox_approval_required"],
                )

            controls.append("sandbox_approval_required")
            if device.trust_level in {"untrusted", "low"}:
                return self._decision(
                    normalized,
                    decision="deny",
                    rule_id="interlock.live.trust_floor.deny",
                    reason=(
                        f"Device trust level {device.trust_level} is below live actuation floor"
                    ),
                    required_controls=controls,
                )

            if normalized.operator_role not in self._live_operator_allowlist:
                return self._decision(
                    normalized,
                    decision="deny",
                    rule_id="interlock.live.operator_role.deny",
                    reason=(
                        f"Operator role {normalized.operator_role} is not authorized for live actuation"
                    ),
                    required_controls=controls,
                )

        risk_tier = capability.risk_tier
        if normalized.execution_mode == "live" and risk_tier == "critical":
            controls.append("human_approval_required")
            return self._decision(
                normalized,
                decision="require_approval",
                rule_id="interlock.live.critical.require_approval",
                reason="Critical-risk physical command requires explicit human approval",
                required_controls=controls,
            )

        if normalized.execution_mode == "live" and risk_tier == "high":
            controls.append("supervisor_ack_required")
            return self._decision(
                normalized,
                decision="require_approval",
                rule_id="interlock.live.high.require_approval",
                reason="High-risk physical command requires supervisor acknowledgment",
                required_controls=controls,
            )

        if (
            normalized.execution_mode == "live"
            and capability.capability_type in {"actuator", "hybrid"}
            and _TRUST_RANK[device.trust_level] == _TRUST_RANK["medium"]
        ):
            controls.append("supervisor_ack_required")
            return self._decision(
                normalized,
                decision="require_approval",
                rule_id="interlock.live.medium_trust.require_approval",
                reason="Medium-trust live actuation requires supervisor acknowledgment",
                required_controls=controls,
            )

        return self._decision(
            normalized,
            decision="allow",
            rule_id="interlock.allow",
            reason="Physical command satisfies interlock policy",
            required_controls=controls,
        )

    @staticmethod
    def _normalize_request(request: PhysicalInterlockRequest) -> PhysicalInterlockRequest:
        if not isinstance(request, PhysicalInterlockRequest):
            raise TypeError("request must be PhysicalInterlockRequest")

        device_id = _normalize_required(request.device_id, "device_id").lower()
        capability_id = _normalize_required(request.capability_id, "capability_id").lower()
        execution_mode = _normalize_execution_mode(request.execution_mode)
        operator_role = _normalize_required(request.operator_role, "operator_role")

        return PhysicalInterlockRequest(
            device_id=device_id,
            capability_id=capability_id,
            execution_mode=execution_mode,
            operator_role=operator_role,
            sandbox_approved=bool(request.sandbox_approved),
        )

    @staticmethod
    def _decision(
        request: PhysicalInterlockRequest,
        *,
        decision: Decision,
        rule_id: str,
        reason: str,
        required_controls: list[str] | tuple[str, ...] | None = None,
    ) -> PhysicalInterlockDecision:
        controls = tuple(sorted(set(required_controls or ())))
        return PhysicalInterlockDecision(
            decision=decision,
            rule_id=rule_id,
            reason=reason,
            required_controls=controls,
            device_id=request.device_id,
            capability_id=request.capability_id,
            execution_mode=request.execution_mode,
        )


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise PhysicalInterlockError(f"{field_name} is required")
    return normalized


def _normalize_execution_mode(value: ExecutionMode | str) -> ExecutionMode:
    normalized = _normalize_required(str(value), "execution_mode").lower()
    if normalized not in {"simulation", "live"}:
        raise PhysicalInterlockError(
            "execution_mode must be simulation or live"
        )
    return normalized  # type: ignore[return-value]
