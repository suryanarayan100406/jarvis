"""Connector manager for host-scoped local and remote transport adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .host_inventory import HostInventoryService, HostRecord

TransportKind = Literal["local", "remote"]

_TRUST_RANK: dict[str, int] = {
    "untrusted": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


class TransportAdapter(Protocol):
    def execute(
        self,
        *,
        host: HostRecord,
        operation: str,
        payload: dict[str, Any],
        identity: str | None = None,
    ) -> dict[str, Any]:
        """Execute a connector operation against the host target."""


@dataclass(frozen=True)
class AdapterRegistration:
    name: str
    transport: TransportKind
    adapter: TransportAdapter
    host_roles: tuple[str, ...] | None
    min_trust_level: str | None


@dataclass(frozen=True)
class ConnectorExecutionResult:
    host_id: str
    hostname: str
    adapter_name: str
    transport: TransportKind
    operation: str
    identity: str | None
    payload: dict[str, Any]
    result: dict[str, Any]


class ConnectorManagerError(ValueError):
    """Raised when connector registration or routing violates scope constraints."""


class ConnectorManager:
    """Routes host operations through policy-scoped local or remote adapters."""

    def __init__(self, inventory: HostInventoryService) -> None:
        self.inventory = inventory
        self._adapters: dict[str, AdapterRegistration] = {}
        self._default_adapters: dict[TransportKind, str | None] = {
            "local": None,
            "remote": None,
        }
        self._role_identity_map: dict[str, str] = {}
        self._host_identity_map: dict[str, str] = {}

    def register_adapter(
        self,
        *,
        name: str,
        adapter: TransportAdapter,
        transport: TransportKind | str,
        host_roles: list[str] | tuple[str, ...] | None = None,
        min_trust_level: str | None = None,
        make_default: bool = False,
    ) -> AdapterRegistration:
        normalized_name = _normalize_required(name, "name").lower()
        if normalized_name in self._adapters:
            raise ConnectorManagerError(f"Adapter already registered: {normalized_name}")

        normalized_transport = self._normalize_transport(transport)
        self._ensure_adapter_contract(adapter, normalized_name)
        normalized_roles = self._normalize_roles(host_roles)
        normalized_trust = self._normalize_trust(min_trust_level) if min_trust_level is not None else None

        registration = AdapterRegistration(
            name=normalized_name,
            transport=normalized_transport,
            adapter=adapter,
            host_roles=normalized_roles,
            min_trust_level=normalized_trust,
        )
        self._adapters[normalized_name] = registration

        if make_default or self._default_adapters[normalized_transport] is None:
            self._default_adapters[normalized_transport] = normalized_name

        return registration

    def list_adapters(self, *, transport: TransportKind | str | None = None) -> list[AdapterRegistration]:
        if transport is None:
            entries = list(self._adapters.values())
        else:
            normalized_transport = self._normalize_transport(transport)
            entries = [entry for entry in self._adapters.values() if entry.transport == normalized_transport]

        entries.sort(key=lambda entry: entry.name)
        return entries

    def set_identity_mapping(
        self,
        *,
        connector_identity: str,
        host_id: str | None = None,
        role: str | None = None,
    ) -> None:
        normalized_identity = _normalize_required(connector_identity, "connector_identity")
        if (host_id is None) == (role is None):
            raise ConnectorManagerError("Exactly one of host_id or role is required")

        if host_id is not None:
            self.inventory.get_host(host_id)
            self._host_identity_map[host_id] = normalized_identity
            return

        assert role is not None
        normalized_role = self._normalize_role(role)
        self._role_identity_map[normalized_role] = normalized_identity

    def execute(
        self,
        host_id: str,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        adapter_name: str | None = None,
        identity: str | None = None,
    ) -> ConnectorExecutionResult:
        host = self.inventory.get_host(host_id)
        registration = self._resolve_registration(host, adapter_name=adapter_name)

        normalized_operation = _normalize_required(operation, "operation")
        request_payload = dict(payload or {})
        request_identity = (
            _normalize_required(identity, "identity")
            if identity is not None
            else self._resolve_identity(host)
        )

        result = registration.adapter.execute(
            host=host,
            operation=normalized_operation,
            payload=request_payload,
            identity=request_identity,
        )
        if not isinstance(result, dict):
            raise ConnectorManagerError(
                f"Adapter {registration.name} returned invalid result type: {type(result).__name__}"
            )

        return ConnectorExecutionResult(
            host_id=host.host_id,
            hostname=host.hostname,
            adapter_name=registration.name,
            transport=registration.transport,
            operation=normalized_operation,
            identity=request_identity,
            payload=dict(request_payload),
            result=dict(result),
        )

    def _resolve_registration(self, host: HostRecord, *, adapter_name: str | None) -> AdapterRegistration:
        if not host.enabled:
            raise ConnectorManagerError(f"Host is disabled: {host.hostname}")

        expected_transport = self._expected_transport(host)

        if adapter_name is None:
            default_name = self._default_adapters[expected_transport]
            if default_name is None:
                raise ConnectorManagerError(f"No default {expected_transport} adapter registered")
            registration = self._adapters[default_name]
        else:
            normalized_name = _normalize_required(adapter_name, "adapter_name").lower()
            registration = self._adapters.get(normalized_name)
            if registration is None:
                raise ConnectorManagerError(f"Unknown adapter: {normalized_name}")

        if registration.transport != expected_transport:
            raise ConnectorManagerError(
                f"Adapter transport mismatch for host {host.hostname}: "
                f"expected {expected_transport}, received {registration.transport}"
            )

        if registration.host_roles is not None and host.role not in registration.host_roles:
            allowed = ", ".join(registration.host_roles)
            raise ConnectorManagerError(
                f"Host role {host.role} is not allowed for adapter {registration.name}. "
                f"Allowed roles: {allowed}"
            )

        if registration.min_trust_level is not None and _TRUST_RANK[host.trust_level] < _TRUST_RANK[
            registration.min_trust_level
        ]:
            raise ConnectorManagerError(
                f"Host trust level {host.trust_level} does not meet adapter minimum "
                f"{registration.min_trust_level}"
            )

        return registration

    @staticmethod
    def _expected_transport(host: HostRecord) -> TransportKind:
        return "local" if host.role == "local" else "remote"

    def _resolve_identity(self, host: HostRecord) -> str | None:
        if host.host_id in self._host_identity_map:
            return self._host_identity_map[host.host_id]
        return self._role_identity_map.get(host.role)

    def _normalize_transport(self, transport: TransportKind | str) -> TransportKind:
        normalized = _normalize_required(str(transport), "transport").lower()
        if normalized not in {"local", "remote"}:
            raise ConnectorManagerError("transport must be local or remote")
        return normalized  # type: ignore[return-value]

    def _normalize_roles(self, host_roles: list[str] | tuple[str, ...] | None) -> tuple[str, ...] | None:
        if host_roles is None:
            return None

        normalized = sorted({_normalize_required(role, "host_role").lower() for role in host_roles})
        if not normalized:
            return None

        unsupported = [role for role in normalized if role not in self.inventory.allowed_roles]
        if unsupported:
            allowed = ", ".join(sorted(self.inventory.allowed_roles))
            raise ConnectorManagerError(
                f"Unsupported host roles for adapter scope: {', '.join(unsupported)}. Allowed: {allowed}"
            )

        return tuple(normalized)

    def _normalize_role(self, role: str) -> str:
        normalized = _normalize_required(role, "role").lower()
        if normalized not in self.inventory.allowed_roles:
            allowed = ", ".join(sorted(self.inventory.allowed_roles))
            raise ConnectorManagerError(f"Unsupported role for identity mapping: {role}. Allowed: {allowed}")
        return normalized

    def _normalize_trust(self, trust_level: str) -> str:
        normalized = _normalize_required(trust_level, "min_trust_level").lower()
        if normalized not in self.inventory.allowed_trust_levels:
            allowed = ", ".join(sorted(self.inventory.allowed_trust_levels))
            raise ConnectorManagerError(
                f"Unsupported min_trust_level: {trust_level}. Allowed: {allowed}"
            )
        return normalized

    @staticmethod
    def _ensure_adapter_contract(adapter: TransportAdapter, name: str) -> None:
        execute = getattr(adapter, "execute", None)
        if execute is None or not callable(execute):
            raise ConnectorManagerError(f"Adapter {name} must expose an execute callable")


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ConnectorManagerError(f"{field_name} is required")
    return normalized
