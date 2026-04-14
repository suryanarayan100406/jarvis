"""Physical device registry with trust-level tagging and capability risk metadata."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from .physical_connector_sdk import (
    PhysicalCapabilityDefinition,
    PhysicalConnectorManifest,
    PhysicalConnectorSDK,
)

_TRUST_LEVELS: set[str] = {"high", "medium", "low", "untrusted"}
_RISK_RANK: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


@dataclass(frozen=True)
class PhysicalDeviceRecord:
    device_id: str
    connector_id: str
    display_name: str
    trust_level: str
    trust_tags: tuple[str, ...]
    enabled: bool
    capabilities: tuple[PhysicalCapabilityDefinition, ...]
    max_risk_tier: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class PhysicalDeviceRegistryError(ValueError):
    """Raised when device registration or trust-tagging constraints are violated."""


class PhysicalDeviceRegistry:
    """Registry for physical devices bound to connector capabilities and trust tags."""

    def __init__(self, connector_sdk: PhysicalConnectorSDK) -> None:
        self.connector_sdk = connector_sdk
        self._devices: dict[str, PhysicalDeviceRecord] = {}

    def register_device(
        self,
        *,
        device_id: str,
        connector_id: str,
        display_name: str,
        trust_level: str,
        allowed_capability_ids: list[str] | tuple[str, ...] | None = None,
        trust_tags: list[str] | tuple[str, ...] | None = None,
        enabled: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> PhysicalDeviceRecord:
        normalized_device_id = _normalize_required(device_id, "device_id").lower()
        if normalized_device_id in self._devices:
            raise PhysicalDeviceRegistryError(f"Physical device already registered: {normalized_device_id}")

        normalized_display_name = _normalize_required(display_name, "display_name")
        normalized_trust_level = _normalize_trust_level(trust_level)
        normalized_trust_tags = _normalize_tags(trust_tags or ())

        manifest = self._get_manifest(connector_id)
        resolved_capabilities = _resolve_capabilities(manifest, allowed_capability_ids)
        max_risk_tier = _compute_max_risk_tier(resolved_capabilities)

        now = _utc_now_iso()
        record = PhysicalDeviceRecord(
            device_id=normalized_device_id,
            connector_id=manifest.connector_id,
            display_name=normalized_display_name,
            trust_level=normalized_trust_level,
            trust_tags=normalized_trust_tags,
            enabled=bool(enabled),
            capabilities=resolved_capabilities,
            max_risk_tier=max_risk_tier,
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        self._devices[normalized_device_id] = record
        return record

    def get_device(self, device_id: str) -> PhysicalDeviceRecord:
        normalized_device_id = _normalize_required(device_id, "device_id").lower()
        record = self._devices.get(normalized_device_id)
        if record is None:
            raise KeyError(f"Unknown physical device: {normalized_device_id}")
        return record

    def update_device(
        self,
        device_id: str,
        *,
        trust_level: str | None = None,
        trust_tags: list[str] | tuple[str, ...] | None = None,
        enabled: bool | None = None,
        allowed_capability_ids: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PhysicalDeviceRecord:
        record = self.get_device(device_id)
        updated = record

        if trust_level is not None:
            updated = replace(updated, trust_level=_normalize_trust_level(trust_level))
        if trust_tags is not None:
            updated = replace(updated, trust_tags=_normalize_tags(trust_tags))
        if enabled is not None:
            updated = replace(updated, enabled=bool(enabled))
        if metadata is not None:
            updated = replace(updated, metadata=dict(metadata))
        if allowed_capability_ids is not None:
            manifest = self._get_manifest(updated.connector_id)
            capabilities = _resolve_capabilities(manifest, allowed_capability_ids)
            updated = replace(
                updated,
                capabilities=capabilities,
                max_risk_tier=_compute_max_risk_tier(capabilities),
            )

        updated = replace(updated, updated_at=_utc_now_iso())
        self._devices[record.device_id] = updated
        return updated

    def tag_device_trust(
        self,
        device_id: str,
        *,
        trust_level: str,
        trust_tags: list[str] | tuple[str, ...] | None = None,
    ) -> PhysicalDeviceRecord:
        return self.update_device(
            device_id,
            trust_level=trust_level,
            trust_tags=trust_tags,
        )

    def get_capability_profile(
        self,
        device_id: str,
        capability_id: str,
    ) -> PhysicalCapabilityDefinition:
        record = self.get_device(device_id)
        normalized_capability_id = _normalize_required(capability_id, "capability_id").lower()
        for capability in record.capabilities:
            if capability.capability_id == normalized_capability_id:
                return capability
        raise PhysicalDeviceRegistryError(
            f"Capability {normalized_capability_id} is not registered for device {record.device_id}"
        )

    def list_devices(
        self,
        *,
        connector_id: str | None = None,
        trust_level: str | None = None,
        trust_tag: str | None = None,
        enabled_only: bool = False,
        min_capability_risk: str | None = None,
    ) -> list[PhysicalDeviceRecord]:
        normalized_connector_id = _normalize_required(connector_id, "connector_id").lower() if connector_id else None
        normalized_trust_level = _normalize_trust_level(trust_level) if trust_level else None
        normalized_trust_tag = _normalize_optional_tag(trust_tag)
        normalized_min_risk = _normalize_risk(min_capability_risk) if min_capability_risk else None

        records = list(self._devices.values())
        if normalized_connector_id is not None:
            records = [record for record in records if record.connector_id == normalized_connector_id]
        if normalized_trust_level is not None:
            records = [record for record in records if record.trust_level == normalized_trust_level]
        if normalized_trust_tag is not None:
            records = [record for record in records if normalized_trust_tag in record.trust_tags]
        if enabled_only:
            records = [record for record in records if record.enabled]
        if normalized_min_risk is not None:
            threshold = _RISK_RANK[normalized_min_risk]
            records = [record for record in records if _RISK_RANK[record.max_risk_tier] >= threshold]

        records.sort(key=lambda record: record.device_id)
        return records

    def remove_device(self, device_id: str) -> None:
        record = self.get_device(device_id)
        self._devices.pop(record.device_id, None)

    def _get_manifest(self, connector_id: str) -> PhysicalConnectorManifest:
        normalized_connector_id = _normalize_required(connector_id, "connector_id").lower()
        try:
            return self.connector_sdk.get_connector(normalized_connector_id).manifest
        except KeyError as exc:
            raise PhysicalDeviceRegistryError(
                f"Unknown physical connector: {normalized_connector_id}"
            ) from exc


def _resolve_capabilities(
    manifest: PhysicalConnectorManifest,
    allowed_capability_ids: list[str] | tuple[str, ...] | None,
) -> tuple[PhysicalCapabilityDefinition, ...]:
    capabilities_by_id = {capability.capability_id: capability for capability in manifest.capabilities}

    if allowed_capability_ids is None:
        return tuple(sorted(capabilities_by_id.values(), key=lambda capability: capability.capability_id))

    normalized_ids = sorted(
        {
            _normalize_required(capability_id, "allowed_capability_id").lower()
            for capability_id in allowed_capability_ids
        }
    )
    if not normalized_ids:
        raise PhysicalDeviceRegistryError("allowed_capability_ids must not be empty when provided")

    missing = [capability_id for capability_id in normalized_ids if capability_id not in capabilities_by_id]
    if missing:
        raise PhysicalDeviceRegistryError(
            "Unknown capability ids for connector "
            f"{manifest.connector_id}: {', '.join(missing)}"
        )

    return tuple(capabilities_by_id[capability_id] for capability_id in normalized_ids)


def _compute_max_risk_tier(capabilities: tuple[PhysicalCapabilityDefinition, ...]) -> str:
    if not capabilities:
        raise PhysicalDeviceRegistryError("At least one capability must be assigned to a device")

    ranked = sorted(capabilities, key=lambda capability: _RISK_RANK[capability.risk_tier], reverse=True)
    return ranked[0].risk_tier


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise PhysicalDeviceRegistryError(f"{field_name} is required")
    return normalized


def _normalize_trust_level(value: str) -> str:
    normalized = _normalize_required(value, "trust_level").lower()
    if normalized not in _TRUST_LEVELS:
        allowed = ", ".join(sorted(_TRUST_LEVELS))
        raise PhysicalDeviceRegistryError(
            f"Unsupported trust_level: {value}. Allowed: {allowed}"
        )
    return normalized


def _normalize_risk(value: str) -> str:
    normalized = _normalize_required(value, "min_capability_risk").lower()
    if normalized not in _RISK_RANK:
        allowed = ", ".join(sorted(_RISK_RANK))
        raise PhysicalDeviceRegistryError(
            f"Unsupported risk tier: {value}. Allowed: {allowed}"
        )
    return normalized


def _normalize_tags(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                _normalize_required(value, "trust_tag").lower()
                for value in values
            }
        )
    )


def _normalize_optional_tag(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).lower()
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
