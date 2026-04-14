"""Physical connector SDK for plugin-based IoT and robotics integrations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from jsonschema import RefResolver
from jsonschema.validators import validator_for

CapabilityType = Literal["sensor", "actuator", "hybrid"]
ConnectorKind = Literal["iot", "robotics", "industrial", "custom"]


class PhysicalConnectorAdapter(Protocol):
    def invoke(
        self,
        *,
        manifest: "PhysicalConnectorManifest",
        capability: "PhysicalCapabilityDefinition",
        payload: dict[str, Any],
        simulation: bool,
        identity: str | None = None,
    ) -> dict[str, Any]:
        """Invoke a capability through the underlying physical connector plugin."""


@dataclass(frozen=True)
class PhysicalCapabilityDefinition:
    capability_id: str
    capability_type: CapabilityType
    command: str
    risk_tier: str
    requires_sandbox_approval: bool
    simulation_supported: bool
    safety_tags: tuple[str, ...]
    telemetry_fields: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PhysicalConnectorManifest:
    schema_version: str
    connector_id: str
    provider: str
    connector_kind: ConnectorKind
    capabilities: tuple[PhysicalCapabilityDefinition, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PhysicalConnectorRegistration:
    manifest: PhysicalConnectorManifest
    connector: PhysicalConnectorAdapter
    registered_at: str


@dataclass(frozen=True)
class PhysicalConnectorExecutionResult:
    connector_id: str
    capability_id: str
    simulation: bool
    sandbox_approved: bool
    identity: str | None
    payload: dict[str, Any]
    result: dict[str, Any]


class PhysicalConnectorSdkError(ValueError):
    """Raised when manifest validation or connector execution violates SDK constraints."""


class PhysicalConnectorSDK:
    """Registry and execution facade for physical connector plugins."""

    def __init__(self, schema_path: str | Path | None = None) -> None:
        self.schema_path = Path(schema_path) if schema_path is not None else _default_schema_path()
        if not self.schema_path.exists():
            raise FileNotFoundError(f"Physical connector capability schema not found: {self.schema_path}")

        common_schema_path = self.schema_path.parent / "common.schema.json"
        if not common_schema_path.exists():
            raise FileNotFoundError(f"Common schema not found: {common_schema_path}")

        self._schema: dict[str, Any] = json.loads(self.schema_path.read_text(encoding="utf-8"))
        common_schema: dict[str, Any] = json.loads(common_schema_path.read_text(encoding="utf-8"))

        validator_class = validator_for(self._schema)
        validator_class.check_schema(self._schema)

        store: dict[str, dict[str, Any]] = {
            "physical-connector-capability.schema.json": self._schema,
            "common.schema.json": common_schema,
        }
        schema_id = self._schema.get("$id")
        common_schema_id = common_schema.get("$id")
        if schema_id:
            store[schema_id] = self._schema
        if common_schema_id:
            store[common_schema_id] = common_schema

        resolver = RefResolver.from_schema(self._schema, store=store)
        self._validator = validator_class(self._schema, resolver=resolver)
        self._registrations: dict[str, PhysicalConnectorRegistration] = {}

    def validate_manifest(self, manifest: dict[str, Any]) -> None:
        if not isinstance(manifest, dict):
            raise TypeError("manifest must be a dictionary")

        errors = sorted(self._validator.iter_errors(manifest), key=lambda err: list(err.absolute_path))
        if errors:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
                for error in errors
            )
            raise PhysicalConnectorSdkError(f"Physical connector manifest validation failed. {details}")

    def register_connector(
        self,
        *,
        manifest: dict[str, Any],
        connector: PhysicalConnectorAdapter,
    ) -> PhysicalConnectorRegistration:
        self.validate_manifest(manifest)
        parsed_manifest = self._parse_manifest(manifest)
        self._ensure_connector_contract(connector, parsed_manifest.connector_id)

        if parsed_manifest.connector_id in self._registrations:
            raise PhysicalConnectorSdkError(
                f"Physical connector already registered: {parsed_manifest.connector_id}"
            )

        registration = PhysicalConnectorRegistration(
            manifest=parsed_manifest,
            connector=connector,
            registered_at=_utc_now_iso(),
        )
        self._registrations[parsed_manifest.connector_id] = registration
        return registration

    def get_connector(self, connector_id: str) -> PhysicalConnectorRegistration:
        normalized_connector_id = _normalize_identifier(connector_id, "connector_id")
        registration = self._registrations.get(normalized_connector_id)
        if registration is None:
            raise KeyError(f"Physical connector not found: {normalized_connector_id}")
        return registration

    def list_connectors(self) -> list[PhysicalConnectorRegistration]:
        return [self._registrations[key] for key in sorted(self._registrations)]

    def list_capabilities(self, connector_id: str) -> list[PhysicalCapabilityDefinition]:
        registration = self.get_connector(connector_id)
        return list(registration.manifest.capabilities)

    def execute(
        self,
        connector_id: str,
        capability_id: str,
        payload: dict[str, Any] | None = None,
        *,
        simulation: bool = True,
        sandbox_approved: bool = False,
        identity: str | None = None,
    ) -> PhysicalConnectorExecutionResult:
        registration = self.get_connector(connector_id)
        normalized_capability_id = _normalize_identifier(capability_id, "capability_id")
        capability = self._resolve_capability(
            registration.manifest,
            normalized_capability_id,
        )

        if simulation and not capability.simulation_supported:
            raise PhysicalConnectorSdkError(
                f"Capability {normalized_capability_id} does not support simulation mode"
            )
        if not simulation and capability.requires_sandbox_approval and not sandbox_approved:
            raise PhysicalConnectorSdkError(
                f"Live execution requires sandbox approval for capability {normalized_capability_id}"
            )

        request_payload = dict(payload or {})
        request_identity = _normalize_required(identity, "identity") if identity is not None else None
        result = registration.connector.invoke(
            manifest=registration.manifest,
            capability=capability,
            payload=request_payload,
            simulation=simulation,
            identity=request_identity,
        )

        if not isinstance(result, dict):
            raise PhysicalConnectorSdkError(
                "Physical connector adapter returned invalid result type: "
                f"{type(result).__name__}"
            )

        return PhysicalConnectorExecutionResult(
            connector_id=registration.manifest.connector_id,
            capability_id=capability.capability_id,
            simulation=simulation,
            sandbox_approved=sandbox_approved,
            identity=request_identity,
            payload=dict(request_payload),
            result=dict(result),
        )

    def _parse_manifest(self, manifest: dict[str, Any]) -> PhysicalConnectorManifest:
        connector_id = _normalize_identifier(str(manifest["connector_id"]), "connector_id")
        provider = _normalize_required(str(manifest["provider"]), "provider")
        schema_version = _normalize_required(str(manifest["schema_version"]), "schema_version")
        connector_kind = _normalize_required(str(manifest["connector_kind"]), "connector_kind")

        capabilities: list[PhysicalCapabilityDefinition] = []
        seen_capabilities: set[str] = set()
        for raw_capability in manifest["capabilities"]:
            capability_id = _normalize_identifier(str(raw_capability["capability_id"]), "capability_id")
            if capability_id in seen_capabilities:
                raise PhysicalConnectorSdkError(
                    f"Duplicate capability_id for connector {connector_id}: {capability_id}"
                )
            seen_capabilities.add(capability_id)

            safety_tags = tuple(
                sorted(
                    {
                        _normalize_required(str(item), "safety_tag")
                        for item in raw_capability.get("safety_tags", [])
                    }
                )
            )
            telemetry_fields = tuple(
                sorted(
                    {
                        _normalize_required(str(item), "telemetry_field")
                        for item in raw_capability.get("telemetry_fields", [])
                    }
                )
            )

            capabilities.append(
                PhysicalCapabilityDefinition(
                    capability_id=capability_id,
                    capability_type=_normalize_required(
                        str(raw_capability["capability_type"]),
                        "capability_type",
                    ).lower(),
                    command=_normalize_required(str(raw_capability["command"]), "command"),
                    risk_tier=_normalize_required(str(raw_capability["risk_tier"]), "risk_tier").lower(),
                    requires_sandbox_approval=bool(raw_capability["requires_sandbox_approval"]),
                    simulation_supported=bool(raw_capability["simulation_supported"]),
                    safety_tags=safety_tags,
                    telemetry_fields=telemetry_fields,
                    metadata=dict(raw_capability.get("metadata", {})),
                )
            )

        return PhysicalConnectorManifest(
            schema_version=schema_version,
            connector_id=connector_id,
            provider=provider,
            connector_kind=connector_kind.lower(),
            capabilities=tuple(capabilities),
            metadata=dict(manifest.get("metadata", {})),
        )

    @staticmethod
    def _ensure_connector_contract(connector: PhysicalConnectorAdapter, connector_id: str) -> None:
        invoke = getattr(connector, "invoke", None)
        if invoke is None or not callable(invoke):
            raise PhysicalConnectorSdkError(
                f"Physical connector {connector_id} must expose an invoke callable"
            )

    @staticmethod
    def _resolve_capability(
        manifest: PhysicalConnectorManifest,
        capability_id: str,
    ) -> PhysicalCapabilityDefinition:
        for capability in manifest.capabilities:
            if capability.capability_id == capability_id:
                return capability
        raise PhysicalConnectorSdkError(
            f"Capability {capability_id} is not registered for connector {manifest.connector_id}"
        )


def _normalize_identifier(value: str, field_name: str) -> str:
    return _normalize_required(value, field_name).lower()


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise PhysicalConnectorSdkError(f"{field_name} is required")
    return normalized


def _default_schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "contracts" / "schemas" / "v1" / "physical-connector-capability.schema.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
