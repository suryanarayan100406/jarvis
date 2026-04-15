"""Feedback telemetry ingestion for live physical mission state tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from runtime.orchestration import OperationalEventBus

from .physical_device_registry import PhysicalDeviceRegistry
from .physical_emergency_stop import PhysicalEmergencyStopManager

TelemetryStatus = Literal["nominal", "degraded", "faulted"]
MissionState = Literal["active", "degraded", "faulted", "emergency_stop"]
ExecutionMode = Literal["live", "simulation"]


@dataclass(frozen=True)
class PhysicalTelemetryIngestionRequest:
    mission_id: str
    device_id: str
    capability_id: str
    telemetry: dict[str, Any]
    status: TelemetryStatus | str = "nominal"
    execution_mode: ExecutionMode | str = "live"
    observed_at: str | None = None
    sequence: int | None = None


@dataclass(frozen=True)
class PhysicalTelemetrySample:
    sample_id: str
    mission_id: str
    device_id: str
    connector_id: str
    capability_id: str
    sequence: int
    status: TelemetryStatus
    telemetry: dict[str, Any]
    observed_at: str
    ingested_at: str


@dataclass(frozen=True)
class PhysicalMissionStateSnapshot:
    mission_id: str
    state: MissionState
    total_samples: int
    device_count: int
    nominal_devices: int
    degraded_devices: int
    faulted_devices: int
    updated_at: str
    latest_by_device: dict[str, PhysicalTelemetrySample]


class PhysicalTelemetryIngestionError(ValueError):
    """Raised when telemetry ingestion fails validation or mission state constraints."""


class PhysicalTelemetryIngestionManager:
    """Ingests live telemetry and maintains mission state snapshots."""

    def __init__(
        self,
        device_registry: PhysicalDeviceRegistry,
        *,
        event_bus: OperationalEventBus | None = None,
        emergency_stop_manager: PhysicalEmergencyStopManager | None = None,
        max_samples_per_mission: int = 1000,
    ) -> None:
        if max_samples_per_mission < 1:
            raise ValueError("max_samples_per_mission must be at least 1")

        self.device_registry = device_registry
        self.event_bus = event_bus
        self.emergency_stop_manager = emergency_stop_manager
        self.max_samples_per_mission = max_samples_per_mission
        self._mission_samples: dict[str, list[PhysicalTelemetrySample]] = {}
        self._latest_by_device: dict[str, dict[str, PhysicalTelemetrySample]] = {}

    def ingest(self, request: PhysicalTelemetryIngestionRequest) -> PhysicalTelemetrySample:
        normalized = self._normalize_request(request)
        if normalized.execution_mode != "live":
            raise PhysicalTelemetryIngestionError(
                "Telemetry ingestion only accepts live execution samples"
            )

        try:
            device = self.device_registry.get_device(normalized.device_id)
            capability = self.device_registry.get_capability_profile(
                normalized.device_id,
                normalized.capability_id,
            )
        except KeyError as exc:
            raise PhysicalTelemetryIngestionError(f"Unknown physical device: {normalized.device_id}") from exc
        except Exception as exc:
            raise PhysicalTelemetryIngestionError(str(exc)) from exc

        mission_samples = self._mission_samples.setdefault(normalized.mission_id, [])
        latest_by_device = self._latest_by_device.setdefault(normalized.mission_id, {})
        previous = latest_by_device.get(normalized.device_id)

        sequence = normalized.sequence
        if sequence is None:
            sequence = (previous.sequence + 1) if previous is not None else 1
        elif previous is not None and sequence <= previous.sequence:
            raise PhysicalTelemetryIngestionError(
                f"sequence must increase for device {normalized.device_id}"
            )

        status = normalized.status
        missing_fields = [
            field
            for field in capability.telemetry_fields
            if field not in normalized.telemetry
        ]
        if missing_fields and status == "nominal":
            status = "degraded"

        sample = PhysicalTelemetrySample(
            sample_id=str(uuid4()),
            mission_id=normalized.mission_id,
            device_id=device.device_id,
            connector_id=device.connector_id,
            capability_id=capability.capability_id,
            sequence=sequence,
            status=status,
            telemetry=dict(normalized.telemetry),
            observed_at=normalized.observed_at,
            ingested_at=_utc_now_iso(),
        )

        mission_samples.append(sample)
        if len(mission_samples) > self.max_samples_per_mission:
            del mission_samples[:-self.max_samples_per_mission]

        latest_by_device[device.device_id] = sample

        snapshot = self.get_mission_state(normalized.mission_id)
        self._publish_events(sample, snapshot, missing_fields)
        return sample

    def get_mission_state(self, mission_id: str) -> PhysicalMissionStateSnapshot:
        normalized_mission_id = _normalize_required(mission_id, "mission_id")
        samples = self._mission_samples.get(normalized_mission_id)
        if not samples:
            raise KeyError(f"Unknown mission telemetry state: {normalized_mission_id}")

        latest_by_device = self._latest_by_device[normalized_mission_id]
        latest_samples = list(latest_by_device.values())

        nominal_devices = sum(1 for item in latest_samples if item.status == "nominal")
        degraded_devices = sum(1 for item in latest_samples if item.status == "degraded")
        faulted_devices = sum(1 for item in latest_samples if item.status == "faulted")

        state: MissionState = "active"
        if self.emergency_stop_manager is not None and self.emergency_stop_manager.is_active():
            state = "emergency_stop"
        elif faulted_devices > 0:
            state = "faulted"
        elif degraded_devices > 0:
            state = "degraded"

        return PhysicalMissionStateSnapshot(
            mission_id=normalized_mission_id,
            state=state,
            total_samples=len(samples),
            device_count=len(latest_samples),
            nominal_devices=nominal_devices,
            degraded_devices=degraded_devices,
            faulted_devices=faulted_devices,
            updated_at=samples[-1].ingested_at,
            latest_by_device={
                key: value
                for key, value in sorted(latest_by_device.items(), key=lambda item: item[0])
            },
        )

    def list_mission_states(self) -> list[PhysicalMissionStateSnapshot]:
        mission_ids = sorted(self._mission_samples.keys())
        return [self.get_mission_state(mission_id) for mission_id in mission_ids]

    def get_mission_samples(
        self,
        mission_id: str,
        *,
        limit: int = 100,
    ) -> list[PhysicalTelemetrySample]:
        if limit < 1:
            raise PhysicalTelemetryIngestionError("limit must be at least 1")

        normalized_mission_id = _normalize_required(mission_id, "mission_id")
        samples = self._mission_samples.get(normalized_mission_id)
        if samples is None:
            raise KeyError(f"Unknown mission telemetry state: {normalized_mission_id}")

        return list(samples[-limit:])

    def clear_mission(self, mission_id: str) -> None:
        normalized_mission_id = _normalize_required(mission_id, "mission_id")
        if normalized_mission_id not in self._mission_samples:
            raise KeyError(f"Unknown mission telemetry state: {normalized_mission_id}")

        self._mission_samples.pop(normalized_mission_id, None)
        self._latest_by_device.pop(normalized_mission_id, None)

    def _publish_events(
        self,
        sample: PhysicalTelemetrySample,
        snapshot: PhysicalMissionStateSnapshot,
        missing_fields: list[str],
    ) -> None:
        if self.event_bus is None:
            return

        self.event_bus.publish(
            event_type="physical.telemetry.sample.ingested",
            severity="info",
            source="physical.telemetry_ingestion",
            message=(
                f"Telemetry ingested for mission {sample.mission_id} device {sample.device_id}"
            ),
            payload={
                "mission_id": sample.mission_id,
                "device_id": sample.device_id,
                "capability_id": sample.capability_id,
                "status": sample.status,
                "sequence": sample.sequence,
                "state": snapshot.state,
            },
        )

        if missing_fields:
            self.event_bus.publish(
                event_type="physical.telemetry.missing_fields",
                severity="warning",
                source="physical.telemetry_ingestion",
                message=(
                    f"Telemetry missing expected fields for device {sample.device_id}"
                ),
                payload={
                    "mission_id": sample.mission_id,
                    "device_id": sample.device_id,
                    "capability_id": sample.capability_id,
                    "missing_fields": list(missing_fields),
                },
            )

        if sample.status == "faulted":
            self.event_bus.publish(
                event_type="physical.telemetry.device.fault",
                severity="critical",
                source="physical.telemetry_ingestion",
                message=f"Fault telemetry detected for device {sample.device_id}",
                payload={
                    "mission_id": sample.mission_id,
                    "device_id": sample.device_id,
                    "capability_id": sample.capability_id,
                },
            )
        elif sample.status == "degraded":
            self.event_bus.publish(
                event_type="physical.telemetry.device.degraded",
                severity="warning",
                source="physical.telemetry_ingestion",
                message=f"Degraded telemetry detected for device {sample.device_id}",
                payload={
                    "mission_id": sample.mission_id,
                    "device_id": sample.device_id,
                    "capability_id": sample.capability_id,
                },
            )

        if snapshot.state == "emergency_stop":
            self.event_bus.publish(
                event_type="physical.telemetry.mission.emergency_stop",
                severity="critical",
                source="physical.telemetry_ingestion",
                message=f"Mission {sample.mission_id} telemetry indicates emergency-stop state",
                payload={
                    "mission_id": sample.mission_id,
                    "device_id": sample.device_id,
                    "capability_id": sample.capability_id,
                },
            )

    @staticmethod
    def _normalize_request(
        request: PhysicalTelemetryIngestionRequest,
    ) -> PhysicalTelemetryIngestionRequest:
        if not isinstance(request, PhysicalTelemetryIngestionRequest):
            raise TypeError("request must be PhysicalTelemetryIngestionRequest")

        if not isinstance(request.telemetry, dict):
            raise PhysicalTelemetryIngestionError("telemetry must be a dictionary")

        execution_mode = _normalize_execution_mode(request.execution_mode)
        status = _normalize_status(request.status)

        observed_at = request.observed_at
        if observed_at is None:
            observed_at = _utc_now_iso()
        else:
            observed_at = _normalize_timestamp(observed_at)

        sequence = request.sequence
        if sequence is not None and sequence < 1:
            raise PhysicalTelemetryIngestionError("sequence must be at least 1")

        return PhysicalTelemetryIngestionRequest(
            mission_id=_normalize_required(request.mission_id, "mission_id"),
            device_id=_normalize_required(request.device_id, "device_id").lower(),
            capability_id=_normalize_required(request.capability_id, "capability_id").lower(),
            telemetry=dict(request.telemetry),
            status=status,
            execution_mode=execution_mode,
            observed_at=observed_at,
            sequence=sequence,
        )


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise PhysicalTelemetryIngestionError(f"{field_name} is required")
    return normalized


def _normalize_status(value: TelemetryStatus | str) -> TelemetryStatus:
    normalized = _normalize_required(str(value), "status").lower()
    if normalized not in {"nominal", "degraded", "faulted"}:
        raise PhysicalTelemetryIngestionError(
            "status must be nominal, degraded, or faulted"
        )
    return normalized  # type: ignore[return-value]


def _normalize_execution_mode(value: ExecutionMode | str) -> ExecutionMode:
    normalized = _normalize_required(str(value), "execution_mode").lower()
    if normalized not in {"live", "simulation"}:
        raise PhysicalTelemetryIngestionError(
            "execution_mode must be live or simulation"
        )
    return normalized  # type: ignore[return-value]


def _normalize_timestamp(value: str) -> str:
    normalized = _normalize_required(value, "observed_at")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
