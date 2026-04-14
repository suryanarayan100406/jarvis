"""Secret manager hardening and rotation workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable
from uuid import uuid4

NowProvider = Callable[[], datetime]


@dataclass(frozen=True)
class SecretRecord:
    secret_id: str
    owner_id: str
    status: str
    version: int
    current_fingerprint: str
    allowed_readers: tuple[str, ...]
    allowed_rotators: tuple[str, ...]
    created_at: str
    updated_at: str
    last_rotated_at: str
    next_rotation_due: str
    rotation_interval_days: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SecretAccessResult:
    secret_id: str
    version: int
    fingerprint: str
    masked_value: str
    value: str | None
    retrieved_at: str


@dataclass(frozen=True)
class SecretRotationResult:
    secret_id: str
    previous_version: int
    new_version: int
    previous_fingerprint: str
    new_fingerprint: str
    rotated_at: str
    reason: str
    actor_id: str


@dataclass(frozen=True)
class SecretAuditEvent:
    event_id: str
    secret_id: str
    event_type: str
    actor_id: str
    version: int
    reason: str | None
    metadata: dict[str, Any]
    created_at: str


class SecretManagerError(ValueError):
    """Raised when secret operations violate security constraints."""


class HardenedSecretManager:
    """Stores secrets with scoped access, auditing, and rotation controls."""

    def __init__(
        self,
        *,
        min_secret_length: int = 12,
        default_rotation_interval_days: int = 30,
        now_provider: NowProvider | None = None,
    ) -> None:
        if min_secret_length < 8:
            raise SecretManagerError("min_secret_length must be at least 8")
        if default_rotation_interval_days < 1:
            raise SecretManagerError("default_rotation_interval_days must be at least 1")

        self.min_secret_length = min_secret_length
        self.default_rotation_interval_days = default_rotation_interval_days
        self._now_provider = now_provider or _utc_now

        self._records: dict[str, SecretRecord] = {}
        self._materials: dict[str, dict[int, str]] = {}
        self._audit: list[SecretAuditEvent] = []

    def create_secret(
        self,
        *,
        secret_id: str,
        value: str,
        owner_id: str,
        allowed_readers: list[str] | tuple[str, ...] | None = None,
        allowed_rotators: list[str] | tuple[str, ...] | None = None,
        rotation_interval_days: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SecretRecord:
        normalized_secret_id = _normalize_required(secret_id, "secret_id")
        if normalized_secret_id in self._records:
            raise SecretManagerError(f"Secret already exists: {normalized_secret_id}")

        normalized_owner = _normalize_required(owner_id, "owner_id")
        normalized_value = _normalize_secret_value(value, min_length=self.min_secret_length)
        interval_days = rotation_interval_days or self.default_rotation_interval_days
        if interval_days < 1:
            raise SecretManagerError("rotation_interval_days must be at least 1")

        readers = _normalize_id_set(allowed_readers or (normalized_owner,))
        rotators = _normalize_id_set(allowed_rotators or (normalized_owner,))
        if normalized_owner not in readers:
            readers = tuple(sorted(set(readers) | {normalized_owner}))
        if normalized_owner not in rotators:
            rotators = tuple(sorted(set(rotators) | {normalized_owner}))

        now = self._now_provider()
        now_iso = _to_iso(now)
        fingerprint = _fingerprint(normalized_value)

        record = SecretRecord(
            secret_id=normalized_secret_id,
            owner_id=normalized_owner,
            status="active",
            version=1,
            current_fingerprint=fingerprint,
            allowed_readers=readers,
            allowed_rotators=rotators,
            created_at=now_iso,
            updated_at=now_iso,
            last_rotated_at=now_iso,
            next_rotation_due=_to_iso(now + timedelta(days=interval_days)),
            rotation_interval_days=interval_days,
            metadata=dict(metadata or {}),
        )
        self._records[normalized_secret_id] = record
        self._materials[normalized_secret_id] = {1: normalized_value}
        self._record_audit(
            secret_id=normalized_secret_id,
            event_type="created",
            actor_id=normalized_owner,
            version=1,
            reason="initial_creation",
            metadata={"rotation_interval_days": interval_days},
        )
        return record

    def get_secret(self, secret_id: str) -> SecretRecord:
        normalized_secret_id = _normalize_required(secret_id, "secret_id")
        record = self._records.get(normalized_secret_id)
        if record is None:
            raise KeyError(f"Unknown secret_id: {normalized_secret_id}")
        return record

    def read_secret(
        self,
        secret_id: str,
        *,
        actor_id: str,
        purpose: str,
        reveal: bool = False,
    ) -> SecretAccessResult:
        record = self.get_secret(secret_id)
        if record.status != "active":
            raise SecretManagerError("Secret is not active")

        normalized_actor = _normalize_required(actor_id, "actor_id")
        normalized_purpose = _normalize_required(purpose, "purpose")
        if normalized_actor not in record.allowed_readers:
            raise SecretManagerError(f"Actor {normalized_actor} is not allowed to read secret {record.secret_id}")

        value = self._materials[record.secret_id][record.version]
        access_result = SecretAccessResult(
            secret_id=record.secret_id,
            version=record.version,
            fingerprint=record.current_fingerprint,
            masked_value=_mask_secret(value),
            value=value if reveal else None,
            retrieved_at=_utc_now_iso(),
        )
        self._record_audit(
            secret_id=record.secret_id,
            event_type="read",
            actor_id=normalized_actor,
            version=record.version,
            reason=normalized_purpose,
            metadata={"reveal": reveal},
        )
        return access_result

    def rotate_secret(
        self,
        secret_id: str,
        *,
        new_value: str,
        actor_id: str,
        reason: str,
        force: bool = False,
    ) -> SecretRotationResult:
        record = self.get_secret(secret_id)
        if record.status != "active":
            raise SecretManagerError("Secret is not active")

        normalized_actor = _normalize_required(actor_id, "actor_id")
        if normalized_actor not in record.allowed_rotators:
            raise SecretManagerError(f"Actor {normalized_actor} is not allowed to rotate secret {record.secret_id}")

        normalized_reason = _normalize_required(reason, "reason")
        normalized_value = _normalize_secret_value(new_value, min_length=self.min_secret_length)
        new_fingerprint = _fingerprint(normalized_value)

        if not force and new_fingerprint == record.current_fingerprint:
            raise SecretManagerError("new_value must differ from current secret value")

        now = self._now_provider()
        now_iso = _to_iso(now)
        new_version = record.version + 1
        self._materials[record.secret_id][new_version] = normalized_value

        updated_record = SecretRecord(
            secret_id=record.secret_id,
            owner_id=record.owner_id,
            status=record.status,
            version=new_version,
            current_fingerprint=new_fingerprint,
            allowed_readers=record.allowed_readers,
            allowed_rotators=record.allowed_rotators,
            created_at=record.created_at,
            updated_at=now_iso,
            last_rotated_at=now_iso,
            next_rotation_due=_to_iso(now + timedelta(days=record.rotation_interval_days)),
            rotation_interval_days=record.rotation_interval_days,
            metadata=dict(record.metadata),
        )
        self._records[record.secret_id] = updated_record

        self._record_audit(
            secret_id=record.secret_id,
            event_type="rotated",
            actor_id=normalized_actor,
            version=new_version,
            reason=normalized_reason,
            metadata={"force": force},
        )

        return SecretRotationResult(
            secret_id=record.secret_id,
            previous_version=record.version,
            new_version=new_version,
            previous_fingerprint=record.current_fingerprint,
            new_fingerprint=new_fingerprint,
            rotated_at=now_iso,
            reason=normalized_reason,
            actor_id=normalized_actor,
        )

    def revoke_secret(self, secret_id: str, *, actor_id: str, reason: str) -> SecretRecord:
        record = self.get_secret(secret_id)
        normalized_actor = _normalize_required(actor_id, "actor_id")
        normalized_reason = _normalize_required(reason, "reason")

        if normalized_actor != record.owner_id and normalized_actor not in record.allowed_rotators:
            raise SecretManagerError(f"Actor {normalized_actor} is not allowed to revoke secret {record.secret_id}")
        if record.status == "revoked":
            return record

        updated = SecretRecord(
            secret_id=record.secret_id,
            owner_id=record.owner_id,
            status="revoked",
            version=record.version,
            current_fingerprint=record.current_fingerprint,
            allowed_readers=record.allowed_readers,
            allowed_rotators=record.allowed_rotators,
            created_at=record.created_at,
            updated_at=_utc_now_iso(),
            last_rotated_at=record.last_rotated_at,
            next_rotation_due=record.next_rotation_due,
            rotation_interval_days=record.rotation_interval_days,
            metadata=dict(record.metadata),
        )
        self._records[record.secret_id] = updated
        self._record_audit(
            secret_id=record.secret_id,
            event_type="revoked",
            actor_id=normalized_actor,
            version=record.version,
            reason=normalized_reason,
            metadata={},
        )
        return updated

    def due_for_rotation(self, *, reference_time: str | None = None) -> list[SecretRecord]:
        now = _parse_iso(reference_time) if reference_time is not None else self._now_provider()
        due = [
            record
            for record in self._records.values()
            if record.status == "active" and _parse_iso(record.next_rotation_due) <= now
        ]
        due.sort(key=lambda item: (item.next_rotation_due, item.secret_id))
        return due

    def list_audit_events(
        self,
        *,
        secret_id: str | None = None,
        event_type: str | None = None,
        actor_id: str | None = None,
    ) -> list[SecretAuditEvent]:
        normalized_secret_id = _normalize_optional(secret_id)
        normalized_event_type = _normalize_optional(event_type)
        normalized_actor_id = _normalize_optional(actor_id)

        events = list(self._audit)
        if normalized_secret_id is not None:
            events = [event for event in events if event.secret_id == normalized_secret_id]
        if normalized_event_type is not None:
            events = [event for event in events if event.event_type == normalized_event_type]
        if normalized_actor_id is not None:
            events = [event for event in events if event.actor_id == normalized_actor_id]
        events.sort(key=lambda event: event.created_at)
        return events

    def _record_audit(
        self,
        *,
        secret_id: str,
        event_type: str,
        actor_id: str,
        version: int,
        reason: str | None,
        metadata: dict[str, Any],
    ) -> None:
        event = SecretAuditEvent(
            event_id=str(uuid4()),
            secret_id=secret_id,
            event_type=event_type,
            actor_id=actor_id,
            version=version,
            reason=reason,
            metadata=dict(metadata),
            created_at=_utc_now_iso(),
        )
        self._audit.append(event)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise SecretManagerError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _normalize_id_set(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = sorted({_normalize_required(value, "identity") for value in values})
    if not normalized:
        raise SecretManagerError("identity set cannot be empty")
    return tuple(normalized)


def _normalize_secret_value(value: str, *, min_length: int) -> str:
    normalized = value.strip()
    if len(normalized) < min_length:
        raise SecretManagerError(f"secret value must be at least {min_length} characters")
    if not any(char.isalpha() for char in normalized):
        raise SecretManagerError("secret value must include alphabetic characters")
    if not any(char.isdigit() for char in normalized):
        raise SecretManagerError("secret value must include numeric characters")
    return normalized


def _fingerprint(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _mask_secret(value: str) -> str:
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


def _parse_iso(value: str) -> datetime:
    normalized = value.strip()
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _to_iso(_utc_now())


__all__ = [
    "HardenedSecretManager",
    "SecretAccessResult",
    "SecretAuditEvent",
    "SecretManagerError",
    "SecretRecord",
    "SecretRotationResult",
]
