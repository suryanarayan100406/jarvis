"""Tests for P9-T4 physical safety interlock engine."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control_plane import (
    PhysicalConnectorSDK,
    PhysicalDeviceRegistry,
    PhysicalInterlockRequest,
    PhysicalSafetyInterlockEngine,
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
            "mode": "simulation" if simulation else "live",
            "capability": capability.capability_id,
        }


class PhysicalSafetyInterlockEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = PhysicalConnectorSDK()
        self.sdk.register_connector(
            manifest=_base_manifest(),
            connector=RecordingPhysicalConnector(),
        )

        self.registry = PhysicalDeviceRegistry(self.sdk)
        self.registry.register_device(
            device_id="sensor-01",
            connector_id="warehouse-robotics",
            display_name="Sensor",
            trust_level="high",
            allowed_capability_ids=["temperature-scan"],
        )
        self.registry.register_device(
            device_id="arm-01",
            connector_id="warehouse-robotics",
            display_name="Arm Medium",
            trust_level="medium",
            allowed_capability_ids=["arm-move"],
        )
        self.registry.register_device(
            device_id="arm-medium-risk",
            connector_id="warehouse-robotics",
            display_name="Arm Medium Risk",
            trust_level="medium",
            allowed_capability_ids=["arm-adjust"],
        )
        self.registry.register_device(
            device_id="arm-low",
            connector_id="warehouse-robotics",
            display_name="Arm Low",
            trust_level="low",
            allowed_capability_ids=["arm-move"],
        )
        self.registry.register_device(
            device_id="critical-arm",
            connector_id="warehouse-robotics",
            display_name="Critical Arm",
            trust_level="high",
            allowed_capability_ids=["arm-critical"],
        )

        self.interlock = PhysicalSafetyInterlockEngine(self.registry)

    def test_allows_sensor_simulation(self) -> None:
        decision = self.interlock.evaluate(
            PhysicalInterlockRequest(
                device_id="sensor-01",
                capability_id="temperature-scan",
                execution_mode="simulation",
                operator_role="system",
            )
        )

        self.assertEqual(decision.decision, "allow")
        self.assertEqual(decision.rule_id, "interlock.allow")

    def test_denies_live_actuation_without_sandbox_approval(self) -> None:
        decision = self.interlock.evaluate(
            PhysicalInterlockRequest(
                device_id="arm-01",
                capability_id="arm-move",
                execution_mode="live",
                operator_role="authorized_operator",
                sandbox_approved=False,
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "interlock.live.sandbox_required.deny")

    def test_denies_live_actuation_for_low_trust_device(self) -> None:
        decision = self.interlock.evaluate(
            PhysicalInterlockRequest(
                device_id="arm-low",
                capability_id="arm-move",
                execution_mode="live",
                operator_role="authorized_operator",
                sandbox_approved=True,
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "interlock.live.trust_floor.deny")

    def test_denies_live_actuation_for_unauthorized_role(self) -> None:
        decision = self.interlock.evaluate(
            PhysicalInterlockRequest(
                device_id="arm-01",
                capability_id="arm-move",
                execution_mode="live",
                operator_role="limited_user",
                sandbox_approved=True,
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "interlock.live.operator_role.deny")

    def test_requires_approval_for_medium_trust_live_actuation(self) -> None:
        decision = self.interlock.evaluate(
            PhysicalInterlockRequest(
                device_id="arm-medium-risk",
                capability_id="arm-adjust",
                execution_mode="live",
                operator_role="authorized_operator",
                sandbox_approved=True,
            )
        )

        self.assertEqual(decision.decision, "require_approval")
        self.assertEqual(decision.rule_id, "interlock.live.medium_trust.require_approval")
        self.assertIn("supervisor_ack_required", decision.required_controls)

    def test_requires_approval_for_critical_live_command(self) -> None:
        decision = self.interlock.evaluate(
            PhysicalInterlockRequest(
                device_id="critical-arm",
                capability_id="arm-critical",
                execution_mode="live",
                operator_role="primary_user",
                sandbox_approved=True,
            )
        )

        self.assertEqual(decision.decision, "require_approval")
        self.assertEqual(decision.rule_id, "interlock.live.critical.require_approval")
        self.assertIn("human_approval_required", decision.required_controls)

    def test_denies_when_device_disabled(self) -> None:
        self.registry.update_device("sensor-01", enabled=False)

        decision = self.interlock.evaluate(
            PhysicalInterlockRequest(
                device_id="sensor-01",
                capability_id="temperature-scan",
                execution_mode="simulation",
                operator_role="system",
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "interlock.device.disabled.deny")



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
            {
                "capability_id": "arm-critical",
                "capability_type": "actuator",
                "command": "move_arm_critical",
                "risk_tier": "critical",
                "requires_sandbox_approval": True,
                "simulation_supported": True,
                "safety_tags": ["motion", "high-force"],
                "metadata": {
                    "axis_count": 6
                },
            },
            {
                "capability_id": "arm-adjust",
                "capability_type": "actuator",
                "command": "adjust_arm",
                "risk_tier": "medium",
                "requires_sandbox_approval": True,
                "simulation_supported": True,
                "safety_tags": ["motion", "calibration"],
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
