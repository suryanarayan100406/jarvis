"""Host inventory service with role and trust-level aware filtering."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class HostRecord:
    host_id: str
    hostname: str
    address: str
    role: str
    trust_level: str
    labels: tuple[str, ...]
    enabled: bool
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class HostInventoryError(ValueError):
    """Raised when inventory operations violate inventory constraints."""


class HostInventoryService:
    """In-memory host inventory registry for local and remote control plane targets."""

    allowed_roles = {"local", "app", "db", "cache", "worker", "gateway"}
    allowed_trust_levels = {"high", "medium", "low", "untrusted"}

    def __init__(self) -> None:
        self._hosts: dict[str, HostRecord] = {}

    def register_host(
        self,
        *,
        hostname: str,
        address: str,
        role: str,
        trust_level: str,
        labels: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
        host_id: str | None = None,
    ) -> HostRecord:
        normalized_hostname = _normalize_required(hostname, "hostname")
        normalized_address = _normalize_required(address, "address")
        normalized_role = self._normalize_role(role)
        normalized_trust = self._normalize_trust(trust_level)
        normalized_labels = _normalize_labels(labels or [])

        self._ensure_unique(normalized_hostname, normalized_address)

        now = _utc_now_iso()
        record = HostRecord(
            host_id=host_id or str(uuid4()),
            hostname=normalized_hostname,
            address=normalized_address,
            role=normalized_role,
            trust_level=normalized_trust,
            labels=normalized_labels,
            enabled=True,
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        self._hosts[record.host_id] = record
        return record

    def get_host(self, host_id: str) -> HostRecord:
        host = self._hosts.get(host_id)
        if host is None:
            raise KeyError(f"Unknown host: {host_id}")
        return host

    def update_host(
        self,
        host_id: str,
        *,
        role: str | None = None,
        trust_level: str | None = None,
        labels: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> HostRecord:
        host = self.get_host(host_id)

        updated = host
        if role is not None:
            updated = replace(updated, role=self._normalize_role(role))
        if trust_level is not None:
            updated = replace(updated, trust_level=self._normalize_trust(trust_level))
        if labels is not None:
            updated = replace(updated, labels=_normalize_labels(labels))
        if metadata is not None:
            updated = replace(updated, metadata=dict(metadata))
        if enabled is not None:
            updated = replace(updated, enabled=bool(enabled))

        updated = replace(updated, updated_at=_utc_now_iso())
        self._hosts[host_id] = updated
        return updated

    def remove_host(self, host_id: str) -> None:
        if host_id not in self._hosts:
            raise KeyError(f"Unknown host: {host_id}")
        del self._hosts[host_id]

    def list_hosts(
        self,
        *,
        role: str | None = None,
        trust_level: str | None = None,
        label: str | None = None,
        enabled_only: bool = False,
    ) -> list[HostRecord]:
        normalized_role = self._normalize_role(role) if role is not None else None
        normalized_trust = self._normalize_trust(trust_level) if trust_level is not None else None
        normalized_label = _normalize_optional(label)

        hosts = list(self._hosts.values())
        if normalized_role is not None:
            hosts = [host for host in hosts if host.role == normalized_role]
        if normalized_trust is not None:
            hosts = [host for host in hosts if host.trust_level == normalized_trust]
        if normalized_label is not None:
            hosts = [host for host in hosts if normalized_label in host.labels]
        if enabled_only:
            hosts = [host for host in hosts if host.enabled]

        hosts.sort(key=lambda host: host.hostname)
        return hosts

    def _ensure_unique(self, hostname: str, address: str) -> None:
        for host in self._hosts.values():
            if host.hostname == hostname:
                raise HostInventoryError(f"Host with hostname already exists: {hostname}")
            if host.address == address:
                raise HostInventoryError(f"Host with address already exists: {address}")

    def _normalize_role(self, role: str) -> str:
        normalized = _normalize_required(role, "role").lower()
        if normalized not in self.allowed_roles:
            allowed = ", ".join(sorted(self.allowed_roles))
            raise HostInventoryError(f"Unsupported host role: {role}. Allowed: {allowed}")
        return normalized

    def _normalize_trust(self, trust_level: str) -> str:
        normalized = _normalize_required(trust_level, "trust_level").lower()
        if normalized not in self.allowed_trust_levels:
            allowed = ", ".join(sorted(self.allowed_trust_levels))
            raise HostInventoryError(f"Unsupported trust level: {trust_level}. Allowed: {allowed}")
        return normalized


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise HostInventoryError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).lower()
    return normalized or None


def _normalize_labels(labels: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = sorted({" ".join(label.split()).lower() for label in labels if " ".join(label.split())})
    return tuple(normalized)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
