"""Geofencing and no-go zone constraints for physical commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .physical_device_registry import PhysicalDeviceRegistry

Decision = Literal["allow", "deny", "require_approval"]
ExecutionMode = Literal["simulation", "live"]
ZoneEnforcement = Literal["deny", "require_approval"]


@dataclass(frozen=True)
class GeofencePoint:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class DeviceWorkspace:
    device_id: str
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float


@dataclass(frozen=True)
class NoGoZone:
    zone_id: str
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float
    enforcement: ZoneEnforcement
    device_ids: tuple[str, ...] | None
    capability_ids: tuple[str, ...] | None
    active_modes: tuple[ExecutionMode, ...]
    reason: str | None


@dataclass(frozen=True)
class GeofenceEvaluationRequest:
    device_id: str
    capability_id: str
    execution_mode: ExecutionMode
    target: GeofencePoint | None = None
    path: tuple[GeofencePoint, ...] = ()


@dataclass(frozen=True)
class GeofenceEvaluationDecision:
    decision: Decision
    rule_id: str
    reason: str
    required_controls: tuple[str, ...]
    violated_zone_ids: tuple[str, ...]
    device_id: str
    capability_id: str
    execution_mode: ExecutionMode


class PhysicalGeofenceError(ValueError):
    """Raised when geofence configuration or evaluation is invalid."""


class PhysicalGeofenceEngine:
    """Evaluates workspace and no-go zone constraints for physical motion commands."""

    def __init__(self, device_registry: PhysicalDeviceRegistry) -> None:
        self.device_registry = device_registry
        self._workspaces: dict[str, DeviceWorkspace] = {}
        self._zones: dict[str, NoGoZone] = {}

    def set_device_workspace(
        self,
        *,
        device_id: str,
        min_x: float,
        max_x: float,
        min_y: float,
        max_y: float,
        min_z: float,
        max_z: float,
    ) -> DeviceWorkspace:
        normalized_device_id = _normalize_required(device_id, "device_id").lower()
        _validate_bounds(min_x=min_x, max_x=max_x, min_y=min_y, max_y=max_y, min_z=min_z, max_z=max_z)

        workspace = DeviceWorkspace(
            device_id=normalized_device_id,
            min_x=float(min_x),
            max_x=float(max_x),
            min_y=float(min_y),
            max_y=float(max_y),
            min_z=float(min_z),
            max_z=float(max_z),
        )
        self._workspaces[normalized_device_id] = workspace
        return workspace

    def get_device_workspace(self, device_id: str) -> DeviceWorkspace:
        normalized_device_id = _normalize_required(device_id, "device_id").lower()
        workspace = self._workspaces.get(normalized_device_id)
        if workspace is None:
            raise KeyError(f"Workspace not configured for device: {normalized_device_id}")
        return workspace

    def remove_device_workspace(self, device_id: str) -> None:
        workspace = self.get_device_workspace(device_id)
        self._workspaces.pop(workspace.device_id, None)

    def register_no_go_zone(
        self,
        *,
        zone_id: str,
        min_x: float,
        max_x: float,
        min_y: float,
        max_y: float,
        min_z: float,
        max_z: float,
        enforcement: ZoneEnforcement | str = "deny",
        device_ids: list[str] | tuple[str, ...] | None = None,
        capability_ids: list[str] | tuple[str, ...] | None = None,
        active_modes: list[ExecutionMode] | tuple[ExecutionMode, ...] | None = None,
        reason: str | None = None,
    ) -> NoGoZone:
        normalized_zone_id = _normalize_required(zone_id, "zone_id").lower()
        _validate_bounds(min_x=min_x, max_x=max_x, min_y=min_y, max_y=max_y, min_z=min_z, max_z=max_z)
        normalized_enforcement = _normalize_zone_enforcement(enforcement)

        normalized_device_ids = _normalize_scope_ids(device_ids, "device_id")
        normalized_capability_ids = _normalize_scope_ids(capability_ids, "capability_id")
        normalized_modes = _normalize_modes(active_modes)

        zone = NoGoZone(
            zone_id=normalized_zone_id,
            min_x=float(min_x),
            max_x=float(max_x),
            min_y=float(min_y),
            max_y=float(max_y),
            min_z=float(min_z),
            max_z=float(max_z),
            enforcement=normalized_enforcement,
            device_ids=normalized_device_ids,
            capability_ids=normalized_capability_ids,
            active_modes=normalized_modes,
            reason=_normalize_optional(reason),
        )
        self._zones[normalized_zone_id] = zone
        return zone

    def remove_no_go_zone(self, zone_id: str) -> None:
        normalized_zone_id = _normalize_required(zone_id, "zone_id").lower()
        if normalized_zone_id not in self._zones:
            raise KeyError(f"Unknown no-go zone: {normalized_zone_id}")
        self._zones.pop(normalized_zone_id, None)

    def list_no_go_zones(self) -> list[NoGoZone]:
        return [self._zones[key] for key in sorted(self._zones)]

    def evaluate(self, request: GeofenceEvaluationRequest) -> GeofenceEvaluationDecision:
        normalized = _normalize_request(request)

        try:
            device = self.device_registry.get_device(normalized.device_id)
            capability = self.device_registry.get_capability_profile(
                normalized.device_id,
                normalized.capability_id,
            )
        except KeyError as exc:
            raise PhysicalGeofenceError(f"Unknown physical device: {normalized.device_id}") from exc
        except Exception as exc:
            raise PhysicalGeofenceError(str(exc)) from exc

        if not device.enabled:
            return _decision(
                normalized,
                decision="deny",
                rule_id="geofence.device.disabled.deny",
                reason=f"Device {device.device_id} is disabled",
            )

        points = _collect_points(normalized)
        if not points:
            return _decision(
                normalized,
                decision="allow",
                rule_id="geofence.allow.no_motion",
                reason="No motion target supplied; geofence constraints not triggered",
            )

        workspace = self._workspaces.get(device.device_id)
        if workspace is None:
            return _decision(
                normalized,
                decision="deny",
                rule_id="geofence.workspace.missing.deny",
                reason=f"Workspace is not configured for device {device.device_id}",
                required_controls=("workspace_configuration_required",),
            )

        if any(not _point_in_bounds(point, workspace) for point in points):
            return _decision(
                normalized,
                decision="deny",
                rule_id="geofence.workspace.boundary.deny",
                reason="Requested trajectory exits configured workspace boundary",
                required_controls=("trajectory_replan_required",),
            )

        deny_zone_ids: list[str] = []
        approval_zone_ids: list[str] = []

        for zone in self._zones.values():
            if not _zone_applies(zone, normalized):
                continue
            if any(_point_in_bounds(point, zone) for point in points):
                if zone.enforcement == "deny":
                    deny_zone_ids.append(zone.zone_id)
                else:
                    approval_zone_ids.append(zone.zone_id)

        if deny_zone_ids:
            deny_zone_ids = sorted(set(deny_zone_ids))
            return _decision(
                normalized,
                decision="deny",
                rule_id="geofence.zone.deny",
                reason=(
                    "Trajectory intersects no-go zone(s): " + ", ".join(deny_zone_ids)
                ),
                violated_zone_ids=tuple(deny_zone_ids),
                required_controls=("trajectory_replan_required",),
            )

        if approval_zone_ids:
            approval_zone_ids = sorted(set(approval_zone_ids))
            return _decision(
                normalized,
                decision="require_approval",
                rule_id="geofence.zone.require_approval",
                reason=(
                    "Trajectory intersects approval zone(s): " + ", ".join(approval_zone_ids)
                ),
                violated_zone_ids=tuple(approval_zone_ids),
                required_controls=(
                    "geofence_override_required",
                    "supervisor_ack_required",
                ),
            )

        return _decision(
            normalized,
            decision="allow",
            rule_id="geofence.allow",
            reason="Trajectory satisfies workspace and no-go zone constraints",
            required_controls=("geofence_monitoring_required",) if normalized.execution_mode == "live" else (),
        )


def _normalize_request(request: GeofenceEvaluationRequest) -> GeofenceEvaluationRequest:
    if not isinstance(request, GeofenceEvaluationRequest):
        raise TypeError("request must be GeofenceEvaluationRequest")

    target = _normalize_point(request.target, "target") if request.target is not None else None
    normalized_path = tuple(_normalize_point(point, "path_point") for point in request.path)

    return GeofenceEvaluationRequest(
        device_id=_normalize_required(request.device_id, "device_id").lower(),
        capability_id=_normalize_required(request.capability_id, "capability_id").lower(),
        execution_mode=_normalize_execution_mode(request.execution_mode),
        target=target,
        path=normalized_path,
    )


def _normalize_point(point: GeofencePoint, field_name: str) -> GeofencePoint:
    if not isinstance(point, GeofencePoint):
        raise TypeError(f"{field_name} must be GeofencePoint")
    return GeofencePoint(x=float(point.x), y=float(point.y), z=float(point.z))


def _collect_points(request: GeofenceEvaluationRequest) -> tuple[GeofencePoint, ...]:
    points: list[GeofencePoint] = []
    if request.target is not None:
        points.append(request.target)
    points.extend(request.path)
    return tuple(points)


def _point_in_bounds(point: GeofencePoint, bounds: DeviceWorkspace | NoGoZone) -> bool:
    return (
        bounds.min_x <= point.x <= bounds.max_x
        and bounds.min_y <= point.y <= bounds.max_y
        and bounds.min_z <= point.z <= bounds.max_z
    )


def _zone_applies(zone: NoGoZone, request: GeofenceEvaluationRequest) -> bool:
    if zone.device_ids is not None and request.device_id not in zone.device_ids:
        return False
    if zone.capability_ids is not None and request.capability_id not in zone.capability_ids:
        return False
    if request.execution_mode not in zone.active_modes:
        return False
    return True


def _decision(
    request: GeofenceEvaluationRequest,
    *,
    decision: Decision,
    rule_id: str,
    reason: str,
    required_controls: tuple[str, ...] = (),
    violated_zone_ids: tuple[str, ...] = (),
) -> GeofenceEvaluationDecision:
    return GeofenceEvaluationDecision(
        decision=decision,
        rule_id=rule_id,
        reason=reason,
        required_controls=tuple(sorted(set(required_controls))),
        violated_zone_ids=tuple(sorted(set(violated_zone_ids))),
        device_id=request.device_id,
        capability_id=request.capability_id,
        execution_mode=request.execution_mode,
    )


def _validate_bounds(
    *,
    min_x: float,
    max_x: float,
    min_y: float,
    max_y: float,
    min_z: float,
    max_z: float,
) -> None:
    if float(min_x) > float(max_x):
        raise PhysicalGeofenceError("min_x must be less than or equal to max_x")
    if float(min_y) > float(max_y):
        raise PhysicalGeofenceError("min_y must be less than or equal to max_y")
    if float(min_z) > float(max_z):
        raise PhysicalGeofenceError("min_z must be less than or equal to max_z")


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise PhysicalGeofenceError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _normalize_zone_enforcement(value: ZoneEnforcement | str) -> ZoneEnforcement:
    normalized = _normalize_required(str(value), "enforcement").lower()
    if normalized not in {"deny", "require_approval"}:
        raise PhysicalGeofenceError("enforcement must be deny or require_approval")
    return normalized  # type: ignore[return-value]


def _normalize_execution_mode(value: ExecutionMode | str) -> ExecutionMode:
    normalized = _normalize_required(str(value), "execution_mode").lower()
    if normalized not in {"simulation", "live"}:
        raise PhysicalGeofenceError("execution_mode must be simulation or live")
    return normalized  # type: ignore[return-value]


def _normalize_modes(
    modes: list[ExecutionMode] | tuple[ExecutionMode, ...] | None,
) -> tuple[ExecutionMode, ...]:
    if modes is None:
        return ("simulation", "live")

    normalized = tuple(sorted({_normalize_execution_mode(mode) for mode in modes}))
    if not normalized:
        raise PhysicalGeofenceError("active_modes must not be empty")
    return normalized


def _normalize_scope_ids(
    values: list[str] | tuple[str, ...] | None,
    field_name: str,
) -> tuple[str, ...] | None:
    if values is None:
        return None

    normalized = tuple(sorted({_normalize_required(value, field_name).lower() for value in values}))
    if not normalized:
        return None
    return normalized
