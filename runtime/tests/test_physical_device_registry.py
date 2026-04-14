"""Tests for P9-T2 physical device registry and trust-level tagging."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control_plane import (
    PhysicalConnectorSDK,
    PhysicalDeviceRegistry,
    PhysicalDeviceRegistryError,
)


class RecordingPhysicalConnector:
    def invoke(
        self,
        *,
        manifest: Any,
        capability: Any,
        payload: dict[str, Any],
        simulation: bool,
        identity: str | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "connector": manifest.connector_id,
            "capability": capability.capability_id,
            "simulation": simulation,
            "identity": identity,
            "payload": dict(payload),
        }


class PhysicalDeviceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = PhysicalConnectorSDK()
        self.sdk.register_connector(
            manifest=_base_manifest(),
            connector=RecordingPhysicalConnector(),
        )
        self.registry = PhysicalDeviceRegistry(self.sdk)

    def test_register_device_attaches_capability_and_risk_metadata(self) -> None:
        record = self.registry.register_device(
            device_id="arm-01",
            connector_id="warehouse-robotics",
            display_name="Primary Arm",
            trust_level="medium",
            trust_tags=["production", "zone-a"],
        )

        self.assertEqual(record.device_id, "arm-01")
        self.assertEqual(record.connector_id, "warehouse-robotics")
        self.assertEqual(record.trust_level, "medium")
        self.assertEqual(record.max_risk_tier, "high")
        self.assertEqual(len(record.capabilities), 2)
        self.assertEqual(record.trust_tags, ("production", "zone-a"))

        arm_profile = self.registry.get_capability_profile("arm-01", "arm-move")
        self.assertEqual(arm_profile.risk_tier, "high")
        self.assertTrue(arm_profile.requires_sandbox_approval)

    def test_register_device_rejects_unknown_connector(self) -> None:
        with self.assertRaises(PhysicalDeviceRegistryError):
            self.registry.register_device(
                device_id="arm-02",
                connector_id="unknown-connector",
                display_name="Missing Connector",
                trust_level="medium",
            )

    def test_register_device_rejects_unknown_allowed_capability(self) -> None:
        with self.assertRaises(PhysicalDeviceRegistryError):
            self.registry.register_device(
                device_id="arm-03",
                connector_id="warehouse-robotics",
                display_name="Bad Capability",
                trust_level="medium",
                allowed_capability_ids=["missing"],
            )

    def test_duplicate_device_id_is_rejected(self) -> None:
        self.registry.register_device(
            device_id="arm-04",
            connector_id="warehouse-robotics",
            display_name="Primary",
            trust_level="low",
        )

        with self.assertRaises(PhysicalDeviceRegistryError):
            self.registry.register_device(
                device_id="arm-04",
                connector_id="warehouse-robotics",
                display_name="Secondary",
                trust_level="low",
            )

    def test_tag_device_trust_updates_level_and_tags(self) -> None:
        self.registry.register_device(
            device_id="arm-05",
            connector_id="warehouse-robotics",
            display_name="Trust Target",
            trust_level="low",
        )

        updated = self.registry.tag_device_trust(
            "arm-05",
            trust_level="high",
            trust_tags=["critical", "production"],
        )

        self.assertEqual(updated.trust_level, "high")
        self.assertEqual(updated.trust_tags, ("critical", "production"))

    def test_list_filters_by_trust_and_risk_threshold(self) -> None:
        self.registry.register_device(
            device_id="sensor-01",
            connector_id="warehouse-robotics",
            display_name="Sensor Node",
            trust_level="high",
            allowed_capability_ids=["temperature-scan"],
            trust_tags=["monitoring"],
        )
        self.registry.register_device(
            device_id="arm-06",
            connector_id="warehouse-robotics",
            display_name="Arm Node",
            trust_level="medium",
            allowed_capability_ids=["arm-move"],
            trust_tags=["production"],
        )

        high_trust = self.registry.list_devices(trust_level="high")
        risky = self.registry.list_devices(min_capability_risk="high")
        tagged = self.registry.list_devices(trust_tag="production")

        self.assertEqual([record.device_id for record in high_trust], ["sensor-01"])
        self.assertEqual([record.device_id for record in risky], ["arm-06"])
        self.assertEqual([record.device_id for record in tagged], ["arm-06"])

    def test_get_capability_profile_rejects_unauthorized_capability(self) -> None:
        self.registry.register_device(
            device_id="sensor-02",
            connector_id="warehouse-robotics",
            display_name="Sensor Node",
            trust_level="high",
            allowed_capability_ids=["temperature-scan"],
        )

        with self.assertRaises(PhysicalDeviceRegistryError):
            self.registry.get_capability_profile("sensor-02", "arm-move")

    def test_remove_device(self) -> None:
        self.registry.register_device(
            device_id="arm-07",
            connector_id="warehouse-robotics",
            display_name="Remove Node",
            trust_level="medium",
        )

        self.registry.remove_device("arm-07")

        with self.assertRaises(KeyError):
            self.registry.get_device("arm-07")



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
