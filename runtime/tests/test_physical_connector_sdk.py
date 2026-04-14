"""Tests for P9-T1 physical connector SDK capability contracts and routing."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control_plane import (
    PhysicalConnectorSDK,
    PhysicalConnectorSdkError,
)


class RecordingPhysicalConnector:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response: dict[str, Any] | None = None

    def invoke(
        self,
        *,
        manifest: Any,
        capability: Any,
        payload: dict[str, Any],
        simulation: bool,
        identity: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "connector_id": manifest.connector_id,
                "capability_id": capability.capability_id,
                "simulation": simulation,
                "payload": dict(payload),
                "identity": identity,
            }
        )
        if self.response is not None:
            return dict(self.response)
        return {
            "status": "ok",
            "connector": manifest.connector_id,
            "capability": capability.capability_id,
            "simulation": simulation,
        }


class InvalidPhysicalConnector:
    pass


class PhysicalConnectorSdkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = PhysicalConnectorSDK()
        self.connector = RecordingPhysicalConnector()

    def test_register_connector_with_valid_manifest(self) -> None:
        registration = self.sdk.register_connector(
            manifest=_base_manifest(),
            connector=self.connector,
        )

        self.assertEqual(registration.manifest.connector_id, "warehouse-robotics")
        self.assertEqual(registration.manifest.connector_kind, "robotics")
        self.assertEqual(len(registration.manifest.capabilities), 2)

        listed = self.sdk.list_connectors()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].manifest.connector_id, "warehouse-robotics")

    def test_register_rejects_manifest_schema_violation(self) -> None:
        manifest = _base_manifest()
        manifest["capabilities"][1]["requires_sandbox_approval"] = False

        with self.assertRaises(PhysicalConnectorSdkError):
            self.sdk.register_connector(manifest=manifest, connector=self.connector)

    def test_register_requires_invoke_callable(self) -> None:
        with self.assertRaises(PhysicalConnectorSdkError):
            self.sdk.register_connector(
                manifest=_base_manifest(),
                connector=InvalidPhysicalConnector(),  # type: ignore[arg-type]
            )

    def test_register_rejects_duplicate_connector_id(self) -> None:
        self.sdk.register_connector(manifest=_base_manifest(), connector=self.connector)

        with self.assertRaises(PhysicalConnectorSdkError):
            self.sdk.register_connector(manifest=_base_manifest(), connector=self.connector)

    def test_execute_routes_simulation_to_connector(self) -> None:
        self.sdk.register_connector(manifest=_base_manifest(), connector=self.connector)

        result = self.sdk.execute(
            "warehouse-robotics",
            "temperature-scan",
            payload={"zone": "A1"},
            identity="ops-bot",
        )

        self.assertTrue(result.simulation)
        self.assertEqual(result.connector_id, "warehouse-robotics")
        self.assertEqual(result.capability_id, "temperature-scan")
        self.assertEqual(result.result["status"], "ok")
        self.assertEqual(len(self.connector.calls), 1)
        self.assertEqual(self.connector.calls[0]["identity"], "ops-bot")
        self.assertEqual(self.connector.calls[0]["payload"]["zone"], "A1")

    def test_execute_live_actuation_requires_sandbox_approval(self) -> None:
        self.sdk.register_connector(manifest=_base_manifest(), connector=self.connector)

        with self.assertRaises(PhysicalConnectorSdkError):
            self.sdk.execute(
                "warehouse-robotics",
                "arm-move",
                payload={"target": "bin-7"},
                simulation=False,
                sandbox_approved=False,
            )

    def test_execute_rejects_simulation_for_live_only_capability(self) -> None:
        manifest = _base_manifest()
        manifest["capabilities"][1]["simulation_supported"] = False
        self.sdk.register_connector(manifest=manifest, connector=self.connector)

        with self.assertRaises(PhysicalConnectorSdkError):
            self.sdk.execute(
                "warehouse-robotics",
                "arm-move",
                payload={"target": "bin-7"},
                simulation=True,
            )

    def test_execute_live_actuation_with_approval_succeeds(self) -> None:
        self.sdk.register_connector(manifest=_base_manifest(), connector=self.connector)

        result = self.sdk.execute(
            "warehouse-robotics",
            "arm-move",
            payload={"target": "bin-7"},
            simulation=False,
            sandbox_approved=True,
        )

        self.assertFalse(result.simulation)
        self.assertTrue(result.sandbox_approved)
        self.assertEqual(self.connector.calls[0]["simulation"], False)

    def test_execute_rejects_unknown_capability(self) -> None:
        self.sdk.register_connector(manifest=_base_manifest(), connector=self.connector)

        with self.assertRaises(PhysicalConnectorSdkError):
            self.sdk.execute("warehouse-robotics", "missing-capability")


def _base_manifest() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "connector_id": "warehouse-robotics",
        "provider": "Acme Robotics",
        "connector_kind": "robotics",
        "capabilities": [
            {
                "capability_id": "temperature-scan",
                "capability_type": "sensor",
                "command": "read_temperature",
                "risk_tier": "low",
                "requires_sandbox_approval": False,
                "simulation_supported": True,
                "safety_tags": ["read-only", "non-invasive"],
                "telemetry_fields": ["temperature_c", "timestamp"],
                "metadata": {
                    "units": "celsius"
                },
            },
            {
                "capability_id": "arm-move",
                "capability_type": "actuator",
                "command": "move_arm",
                "risk_tier": "high",
                "requires_sandbox_approval": True,
                "simulation_supported": True,
                "safety_tags": ["motion", "pinch-hazard"],
                "metadata": {
                    "axis_count": 6
                },
            },
        ],
        "metadata": {
            "site": "lab-a"
        },
    }


if __name__ == "__main__":
    unittest.main()
