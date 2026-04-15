"""Manual takeover and override workflows for physical mission execution."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from runtime.orchestration import OperationalEventBus

from .physical_emergency_stop import PhysicalEmergencyStopManager
from .physical_telemetry_ingestion import PhysicalTelemetryIngestionManager

TakeoverState = Literal["active", "released"]
EventSeverity = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class PhysicalManualTakeoverSession:
    session_id: str
    mission_id: str
    operator_id: str
    operator_role: str
    state: TakeoverState
    reason: str
    mission_state: str | None
    started_at: str
    released_at: str | None


@dataclass(frozen=True)
class PhysicalManualOverrideGrant:
    override_id: str
    session_id: str
    mission_id: str
    operator_id: str
    operator_role: str
    reason: str
    device_id: str | None
    capability_id: str | None
    required_controls: tuple[str, ...]
    single_use: bool
    issued_at: str
    expires_at: str | None
    consumed_at: str | None
    revoked_at: str | None


@dataclass(frozen=True)
class PhysicalManualTakeoverEvent:
    event_id: str
    timestamp: str
    event_type: str
    severity: EventSeverity
    mission_id: str
    session_id: str | None
    operator_id: str
    operator_role: str
    reason: str
    payload: dict[str, Any]


class PhysicalManualTakeoverError(ValueError):
    """Raised when manual takeover or override workflow constraints are violated."""


class PhysicalManualTakeoverManager:
    """Manages human takeover sessions and scoped override grants for missions."""

    _authorized_roles = {"primary_user", "authorized_operator", "supervisor"}
    _privileged_roles = {"primary_user", "supervisor"}

    def __init__(
        self,
        *,
        telemetry_ingestion: PhysicalTelemetryIngestionManager | None = None,
        emergency_stop_manager: PhysicalEmergencyStopManager | None = None,
        event_bus: OperationalEventBus | None = None,
    ) -> None:
        self.telemetry_ingestion = telemetry_ingestion
        self.emergency_stop_manager = emergency_stop_manager
        self.event_bus = event_bus

        self._active_sessions: dict[str, PhysicalManualTakeoverSession] = {}
        self._sessions: dict[str, PhysicalManualTakeoverSession] = {}
        self._overrides: dict[str, PhysicalManualOverrideGrant] = {}
        self._mission_override_order: dict[str, list[str]] = {}
        self._history: list[PhysicalManualTakeoverEvent] = []

    def activate_takeover(
        self,
        *,
        mission_id: str,
        operator_id: str,
        operator_role: str,
        reason: str,
    ) -> PhysicalManualTakeoverSession:
        normalized_mission_id = _normalize_required(mission_id, "mission_id")
        normalized_operator_id = _normalize_required(operator_id, "operator_id")
        normalized_operator_role = _normalize_role(operator_role, self._authorized_roles)
        normalized_reason = _normalize_required(reason, "reason")

        existing = self._active_sessions.get(normalized_mission_id)
        if existing is not None:
            if existing.operator_id != normalized_operator_id and normalized_operator_role not in self._privileged_roles:
                raise PhysicalManualTakeoverError(
                    f"Mission {normalized_mission_id} is already under manual takeover by operator {existing.operator_id}"
                )
            return existing

        mission_state: str | None = None
        if self.telemetry_ingestion is not None:
            try:
                mission_snapshot = self.telemetry_ingestion.get_mission_state(normalized_mission_id)
            except KeyError as exc:
                raise PhysicalManualTakeoverError(
                    f"Mission {normalized_mission_id} has no active telemetry state"
                ) from exc
            mission_state = mission_snapshot.state

        session = PhysicalManualTakeoverSession(
            session_id=str(uuid4()),
            mission_id=normalized_mission_id,
            operator_id=normalized_operator_id,
            operator_role=normalized_operator_role,
            state="active",
            reason=normalized_reason,
            mission_state=mission_state,
            started_at=_utc_now_iso(),
            released_at=None,
        )

        self._active_sessions[normalized_mission_id] = session
        self._sessions[session.session_id] = session
        self._mission_override_order.setdefault(normalized_mission_id, [])

        self._emit_event(
            event_type="physical.takeover.activated",
            severity="critical",
            mission_id=normalized_mission_id,
            session_id=session.session_id,
            operator_id=normalized_operator_id,
            operator_role=normalized_operator_role,
            reason=normalized_reason,
            message=(
                f"Manual takeover activated for mission {normalized_mission_id} by {normalized_operator_id}"
            ),
            payload={
                "mission_state": mission_state,
            },
        )
        return session

    def release_takeover(
        self,
        *,
        mission_id: str,
        operator_id: str,
        operator_role: str,
        reason: str,
        revoke_pending_overrides: bool = True,
    ) -> PhysicalManualTakeoverSession:
        normalized_mission_id = _normalize_required(mission_id, "mission_id")
        normalized_operator_id = _normalize_required(operator_id, "operator_id")
        normalized_operator_role = _normalize_role(operator_role, self._authorized_roles)
        normalized_reason = _normalize_required(reason, "reason")

        active = self._active_sessions.get(normalized_mission_id)
        if active is None:
            raise PhysicalManualTakeoverError(
                f"Mission {normalized_mission_id} is not under manual takeover"
            )

        if active.operator_id != normalized_operator_id and normalized_operator_role not in self._privileged_roles:
            raise PhysicalManualTakeoverError(
                f"Operator {normalized_operator_id} is not allowed to release takeover for mission {normalized_mission_id}"
            )

        released_at = _utc_now_iso()
        released = replace(active, state="released", released_at=released_at)

        self._active_sessions.pop(normalized_mission_id, None)
        self._sessions[released.session_id] = released

        revoked_count = 0
        if revoke_pending_overrides:
            for override_id in self._mission_override_order.get(normalized_mission_id, []):
                grant = self._overrides[override_id]
                if _is_override_usable(grant, at=_parse_iso(released_at)):
                    self._overrides[override_id] = replace(grant, revoked_at=released_at)
                    revoked_count += 1

        self._emit_event(
            event_type="physical.takeover.released",
            severity="warning",
            mission_id=normalized_mission_id,
            session_id=released.session_id,
            operator_id=normalized_operator_id,
            operator_role=normalized_operator_role,
            reason=normalized_reason,
            message=f"Manual takeover released for mission {normalized_mission_id}",
            payload={
                "revoked_overrides": revoked_count,
            },
        )
        return released

    def grant_override(
        self,
        *,
        mission_id: str,
        operator_id: str,
        operator_role: str,
        reason: str,
        device_id: str | None = None,
        capability_id: str | None = None,
        required_controls: list[str] | tuple[str, ...] | None = None,
        single_use: bool = True,
        expires_in_seconds: int | None = 300,
    ) -> PhysicalManualOverrideGrant:
        normalized_mission_id = _normalize_required(mission_id, "mission_id")
        normalized_operator_id = _normalize_required(operator_id, "operator_id")
        normalized_operator_role = _normalize_role(operator_role, self._authorized_roles)
        normalized_reason = _normalize_required(reason, "reason")

        active_session = self._active_sessions.get(normalized_mission_id)
        if active_session is None:
            raise PhysicalManualTakeoverError(
                f"Mission {normalized_mission_id} is not under manual takeover"
            )

        if active_session.operator_id != normalized_operator_id and normalized_operator_role not in self._privileged_roles:
            raise PhysicalManualTakeoverError(
                f"Operator {normalized_operator_id} is not allowed to grant override for mission {normalized_mission_id}"
            )

        if self.emergency_stop_manager is not None and self.emergency_stop_manager.is_active():
            raise PhysicalManualTakeoverError(
                "Cannot grant manual override while emergency stop is active"
            )

        normalized_device_id = _normalize_optional_identifier(device_id)
        normalized_capability_id = _normalize_optional_identifier(capability_id)
        normalized_controls = _normalize_controls(required_controls or ())

        if expires_in_seconds is not None and expires_in_seconds < 1:
            raise PhysicalManualTakeoverError("expires_in_seconds must be at least 1")

        issued_at = _utc_now_iso()
        expires_at = (
            _to_iso(_parse_iso(issued_at) + timedelta(seconds=expires_in_seconds))
            if expires_in_seconds is not None
            else None
        )

        grant = PhysicalManualOverrideGrant(
            override_id=str(uuid4()),
            session_id=active_session.session_id,
            mission_id=normalized_mission_id,
            operator_id=normalized_operator_id,
            operator_role=normalized_operator_role,
            reason=normalized_reason,
            device_id=normalized_device_id,
            capability_id=normalized_capability_id,
            required_controls=normalized_controls,
            single_use=bool(single_use),
            issued_at=issued_at,
            expires_at=expires_at,
            consumed_at=None,
            revoked_at=None,
        )

        self._overrides[grant.override_id] = grant
        self._mission_override_order.setdefault(normalized_mission_id, []).append(grant.override_id)

        self._emit_event(
            event_type="physical.takeover.override.granted",
            severity="warning",
            mission_id=normalized_mission_id,
            session_id=active_session.session_id,
            operator_id=normalized_operator_id,
            operator_role=normalized_operator_role,
            reason=normalized_reason,
            message=f"Manual override granted for mission {normalized_mission_id}",
            payload={
                "override_id": grant.override_id,
                "single_use": grant.single_use,
                "device_id": grant.device_id,
                "capability_id": grant.capability_id,
                "required_controls": list(grant.required_controls),
                "expires_at": grant.expires_at,
            },
        )
        return grant

    def revoke_override(
        self,
        *,
        override_id: str,
        operator_id: str,
        operator_role: str,
        reason: str,
    ) -> PhysicalManualOverrideGrant:
        normalized_override_id = _normalize_required(override_id, "override_id")
        normalized_operator_id = _normalize_required(operator_id, "operator_id")
        normalized_operator_role = _normalize_role(operator_role, self._authorized_roles)
        normalized_reason = _normalize_required(reason, "reason")

        grant = self._overrides.get(normalized_override_id)
        if grant is None:
            raise KeyError(f"Unknown override grant: {normalized_override_id}")

        if grant.operator_id != normalized_operator_id and normalized_operator_role not in self._privileged_roles:
            raise PhysicalManualTakeoverError(
                f"Operator {normalized_operator_id} is not allowed to revoke override {normalized_override_id}"
            )

        if grant.revoked_at is not None:
            return grant

        revoked_at = _utc_now_iso()
        updated = replace(grant, revoked_at=revoked_at)
        self._overrides[normalized_override_id] = updated

        self._emit_event(
            event_type="physical.takeover.override.revoked",
            severity="warning",
            mission_id=updated.mission_id,
            session_id=updated.session_id,
            operator_id=normalized_operator_id,
            operator_role=normalized_operator_role,
            reason=normalized_reason,
            message=f"Manual override {normalized_override_id} revoked",
            payload={
                "override_id": normalized_override_id,
            },
        )
        return updated

    def assert_can_execute(
        self,
        *,
        mission_id: str,
        device_id: str,
        capability_id: str,
        required_controls: list[str] | tuple[str, ...] | None = None,
    ) -> PhysicalManualOverrideGrant | None:
        normalized_mission_id = _normalize_required(mission_id, "mission_id")
        normalized_device_id = _normalize_required(device_id, "device_id").lower()
        normalized_capability_id = _normalize_required(capability_id, "capability_id").lower()
        normalized_controls = _normalize_controls(required_controls or ())

        active_session = self._active_sessions.get(normalized_mission_id)
        if active_session is None:
            return None

        now = datetime.now(timezone.utc)
        grant = self._find_matching_override(
            mission_id=normalized_mission_id,
            device_id=normalized_device_id,
            capability_id=normalized_capability_id,
            required_controls=normalized_controls,
            at=now,
        )
        if grant is None:
            raise PhysicalManualTakeoverError(
                f"Mission {normalized_mission_id} is under manual takeover and has no matching override"
            )

        if grant.single_use and grant.consumed_at is None:
            consumed_at = _to_iso(now)
            grant = replace(grant, consumed_at=consumed_at)
            self._overrides[grant.override_id] = grant
            self._emit_event(
                event_type="physical.takeover.override.consumed",
                severity="info",
                mission_id=normalized_mission_id,
                session_id=active_session.session_id,
                operator_id=grant.operator_id,
                operator_role=grant.operator_role,
                reason=grant.reason,
                message=f"Manual override {grant.override_id} consumed",
                payload={
                    "override_id": grant.override_id,
                    "device_id": normalized_device_id,
                    "capability_id": normalized_capability_id,
                },
            )

        return grant

    def is_takeover_active(self, mission_id: str | None = None) -> bool:
        if mission_id is None:
            return bool(self._active_sessions)
        normalized_mission_id = _normalize_required(mission_id, "mission_id")
        return normalized_mission_id in self._active_sessions

    def get_active_takeover(self, mission_id: str) -> PhysicalManualTakeoverSession:
        normalized_mission_id = _normalize_required(mission_id, "mission_id")
        session = self._active_sessions.get(normalized_mission_id)
        if session is None:
            raise KeyError(f"Mission {normalized_mission_id} is not under manual takeover")
        return session

    def list_takeovers(self, *, active_only: bool = False) -> list[PhysicalManualTakeoverSession]:
        sessions = list(self._sessions.values())
        if active_only:
            sessions = [session for session in sessions if session.state == "active"]
        sessions.sort(key=lambda session: (session.started_at, session.session_id), reverse=True)
        return sessions

    def list_overrides(
        self,
        mission_id: str,
        *,
        include_inactive: bool = False,
    ) -> list[PhysicalManualOverrideGrant]:
        normalized_mission_id = _normalize_required(mission_id, "mission_id")
        now = datetime.now(timezone.utc)
        overrides: list[PhysicalManualOverrideGrant] = []

        for override_id in self._mission_override_order.get(normalized_mission_id, []):
            grant = self._overrides[override_id]
            if include_inactive or _is_override_usable(grant, at=now):
                overrides.append(grant)

        overrides.sort(key=lambda item: (item.issued_at, item.override_id), reverse=True)
        return overrides

    @property
    def history(self) -> list[PhysicalManualTakeoverEvent]:
        return list(self._history)

    def _find_matching_override(
        self,
        *,
        mission_id: str,
        device_id: str,
        capability_id: str,
        required_controls: tuple[str, ...],
        at: datetime,
    ) -> PhysicalManualOverrideGrant | None:
        candidates = self._mission_override_order.get(mission_id, [])
        for override_id in reversed(candidates):
            grant = self._overrides[override_id]
            if not _is_override_usable(grant, at=at):
                continue

            if grant.device_id is not None and grant.device_id != device_id:
                continue
            if grant.capability_id is not None and grant.capability_id != capability_id:
                continue
            if not set(required_controls).issubset(set(grant.required_controls)):
                continue

            return grant

        return None

    def _emit_event(
        self,
        *,
        event_type: str,
        severity: EventSeverity,
        mission_id: str,
        session_id: str | None,
        operator_id: str,
        operator_role: str,
        reason: str,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        event = PhysicalManualTakeoverEvent(
            event_id=str(uuid4()),
            timestamp=_utc_now_iso(),
            event_type=_normalize_required(event_type, "event_type").lower(),
            severity=severity,
            mission_id=mission_id,
            session_id=session_id,
            operator_id=operator_id,
            operator_role=operator_role,
            reason=reason,
            payload=dict(payload),
        )
        self._history.append(event)

        if self.event_bus is not None:
            self.event_bus.publish(
                event_type=event.event_type,
                severity=event.severity,
                source="physical.manual_takeover",
                message=_normalize_required(message, "message"),
                payload={
                    "mission_id": mission_id,
                    "session_id": session_id,
                    "operator_id": operator_id,
                    "operator_role": operator_role,
                    "reason": reason,
                    **payload,
                },
            )


def _is_override_usable(grant: PhysicalManualOverrideGrant, *, at: datetime) -> bool:
    if grant.revoked_at is not None:
        return False
    if grant.single_use and grant.consumed_at is not None:
        return False
    if grant.expires_at is not None and _parse_iso(grant.expires_at) <= at:
        return False
    return True


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise PhysicalManualTakeoverError(f"{field_name} is required")
    return normalized


def _normalize_optional_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _normalize_required(value, "identifier").lower()
    return normalized


def _normalize_controls(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({_normalize_required(value, "control").lower() for value in values}))


def _normalize_role(value: str, allowed_roles: set[str]) -> str:
    normalized = _normalize_required(value, "operator_role").lower()
    if normalized not in allowed_roles:
        allowed = ", ".join(sorted(allowed_roles))
        raise PhysicalManualTakeoverError(
            f"Unsupported operator_role {value}. Allowed: {allowed}"
        )
    return normalized


def _parse_iso(value: str) -> datetime:
    normalized = _normalize_required(value, "timestamp")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now_iso() -> str:
    return _to_iso(datetime.now(timezone.utc))
