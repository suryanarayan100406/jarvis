"""Emergency stop propagation manager for physical connectors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from runtime.control import KillSwitchController, KillSwitchEvent

from .physical_connector_sdk import PhysicalCapabilityDefinition, PhysicalConnectorSDK
from .physical_device_registry import PhysicalDeviceRecord, PhysicalDeviceRegistry

DispatchStatus = Literal["dispatched", "failed", "skipped"]


@dataclass(frozen=True)
class PhysicalEmergencyStopDispatchResult:
    device_id: str
    connector_id: str
    capability_id: str | None
    status: DispatchStatus
    result: dict[str, Any] | None
    error: str | None


@dataclass(frozen=True)
class PhysicalEmergencyStopEvent:
    event_id: str
    timestamp: str
    state: str
    reason: str
    actor: str
    source: str
    dispatched: int
    failed: int
    skipped: int
    results: tuple[PhysicalEmergencyStopDispatchResult, ...]


class PhysicalEmergencyStopError(ValueError):
    """Raised when emergency-stop orchestration receives invalid input."""


class PhysicalEmergencyStopManager:
    """Propagates emergency-stop signals to registered physical connectors."""

    def __init__(
        self,
        connector_sdk: PhysicalConnectorSDK,
        device_registry: PhysicalDeviceRegistry,
    ) -> None:
        self.connector_sdk = connector_sdk
        self.device_registry = device_registry
        self._active = False
        self._history: list[PhysicalEmergencyStopEvent] = []
        self._device_capability_overrides: dict[str, str] = {}
        self._kill_switch_hook_registered = False

    def configure_device_emergency_capability(self, device_id: str, capability_id: str) -> None:
        normalized_device_id = _normalize_required(device_id, "device_id").lower()
        normalized_capability_id = _normalize_required(capability_id, "capability_id").lower()

        try:
            self.device_registry.get_capability_profile(normalized_device_id, normalized_capability_id)
        except Exception as exc:
            raise PhysicalEmergencyStopError(str(exc)) from exc

        self._device_capability_overrides[normalized_device_id] = normalized_capability_id

    def register_kill_switch_hook(
        self,
        kill_switch: KillSwitchController,
        *,
        hook_name: str = "physical_emergency_stop",
    ) -> None:
        normalized_hook_name = _normalize_required(hook_name, "hook_name")

        if self._kill_switch_hook_registered:
            raise PhysicalEmergencyStopError("Kill-switch hook already registered")

        def _halt_hook(event: KillSwitchEvent) -> None:
            self.activate(
                reason=event.reason,
                actor=event.actor,
                source="kill_switch_hook",
            )

        kill_switch.register_halt_hook(normalized_hook_name, _halt_hook)
        self._kill_switch_hook_registered = True

    def activate(
        self,
        *,
        reason: str,
        actor: str = "system",
        source: str = "manual",
    ) -> PhysicalEmergencyStopEvent:
        normalized_reason = _normalize_required(reason, "reason")
        normalized_actor = _normalize_required(actor, "actor")
        normalized_source = _normalize_required(source, "source")

        if self._active and self._history and self._history[-1].state == "active":
            return self._history[-1]

        results: list[PhysicalEmergencyStopDispatchResult] = []
        devices = self.device_registry.list_devices(enabled_only=True)

        for device in devices:
            capability = self._resolve_emergency_capability(device)
            if capability is None:
                results.append(
                    PhysicalEmergencyStopDispatchResult(
                        device_id=device.device_id,
                        connector_id=device.connector_id,
                        capability_id=None,
                        status="skipped",
                        result=None,
                        error="No emergency-stop capability configured for device",
                    )
                )
                continue

            payload = {
                "emergency_stop": True,
                "device_id": device.device_id,
                "reason": normalized_reason,
                "actor": normalized_actor,
                "source": normalized_source,
            }

            try:
                execution = self.connector_sdk.execute(
                    connector_id=device.connector_id,
                    capability_id=capability.capability_id,
                    payload=payload,
                    simulation=False,
                    sandbox_approved=True,
                    identity="system-emergency-stop",
                )
                results.append(
                    PhysicalEmergencyStopDispatchResult(
                        device_id=device.device_id,
                        connector_id=device.connector_id,
                        capability_id=capability.capability_id,
                        status="dispatched",
                        result=dict(execution.result),
                        error=None,
                    )
                )
            except Exception as exc:
                results.append(
                    PhysicalEmergencyStopDispatchResult(
                        device_id=device.device_id,
                        connector_id=device.connector_id,
                        capability_id=capability.capability_id,
                        status="failed",
                        result=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

        event = PhysicalEmergencyStopEvent(
            event_id=str(uuid4()),
            timestamp=_utc_now_iso(),
            state="active",
            reason=normalized_reason,
            actor=normalized_actor,
            source=normalized_source,
            dispatched=sum(1 for item in results if item.status == "dispatched"),
            failed=sum(1 for item in results if item.status == "failed"),
            skipped=sum(1 for item in results if item.status == "skipped"),
            results=tuple(results),
        )

        self._active = True
        self._history.append(event)
        return event

    def reset(self, *, reason: str = "manual_reset", actor: str = "system") -> PhysicalEmergencyStopEvent:
        normalized_reason = _normalize_required(reason, "reason")
        normalized_actor = _normalize_required(actor, "actor")

        event = PhysicalEmergencyStopEvent(
            event_id=str(uuid4()),
            timestamp=_utc_now_iso(),
            state="inactive",
            reason=normalized_reason,
            actor=normalized_actor,
            source="manual_reset",
            dispatched=0,
            failed=0,
            skipped=0,
            results=(),
        )

        self._active = False
        self._history.append(event)
        return event

    def is_active(self) -> bool:
        return self._active

    def assert_can_execute(self) -> None:
        if self._active:
            raise PhysicalEmergencyStopError("Physical execution blocked: emergency stop is active")

    @property
    def history(self) -> list[PhysicalEmergencyStopEvent]:
        return list(self._history)

    def _resolve_emergency_capability(
        self,
        device: PhysicalDeviceRecord,
    ) -> PhysicalCapabilityDefinition | None:
        override = self._device_capability_overrides.get(device.device_id)
        if override is not None:
            for capability in device.capabilities:
                if capability.capability_id == override:
                    return capability

        scored: list[tuple[int, PhysicalCapabilityDefinition]] = []
        for capability in device.capabilities:
            if capability.capability_type not in {"actuator", "hybrid"}:
                continue
            score = _emergency_score(capability)
            if score > 0:
                scored.append((score, capability))

        if not scored:
            return None

        scored.sort(key=lambda item: (-item[0], item[1].capability_id))
        return scored[0][1]


def _emergency_score(capability: PhysicalCapabilityDefinition) -> int:
    score = 0
    capability_id = capability.capability_id.lower()
    command = capability.command.lower()
    tags = {tag.lower() for tag in capability.safety_tags}

    if "emergency-stop" in tags or "e-stop" in tags or "estop" in tags:
        score += 5
    if "emergency" in capability_id or "emergency" in command:
        score += 3
    if "stop" in capability_id or "stop" in command:
        score += 2

    return score


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise PhysicalEmergencyStopError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
