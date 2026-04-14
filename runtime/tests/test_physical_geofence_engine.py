"""Tests for P9-T5 geofencing and no-go zone constraints."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control_plane import (
    GeofenceEvaluationRequest,
    GeofencePoint,
    PhysicalConnectorSDK,
    PhysicalDeviceRegistry,
    PhysicalGeofenceEngine,
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


class PhysicalGeofenceEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = PhysicalConnectorSDK()
        self.sdk.register_connector(manifest=_base_manifest(), connector=RecordingPhysicalConnector())

        self.registry = PhysicalDeviceRegistry(self.sdk)
        self.registry.register_device(
            device_id="arm-01",
            connector_id="warehouse-robotics",
            display_name="Arm",
            trust_level="high",
            allowed_capability_ids=["arm-move"],
        )
        self.registry.register_device(
            device_id="sensor-01",
            connector_id="warehouse-robotics",
            display_name="Sensor",
            trust_level="high",
            allowed_capability_ids=["temperature-scan"],
        )

        self.engine = PhysicalGeofenceEngine(self.registry)
        self.engine.set_device_workspace(
            device_id="arm-01",
            min_x=0,
            max_x=10,
            min_y=0,
            max_y=10,
            min_z=0,
            max_z=5,
        )

    def test_allows_trajectory_within_workspace(self) -> None:
        decision = self.engine.evaluate(
            GeofenceEvaluationRequest(
                device_id="arm-01",
                capability_id="arm-move",
                execution_mode="live",
                target=GeofencePoint(5, 5, 2),
            )
        )

        self.assertEqual(decision.decision, "allow")
        self.assertEqual(decision.rule_id, "geofence.allow")

    def test_denies_when_workspace_missing_for_motion(self) -> None:
        decision = self.engine.evaluate(
            GeofenceEvaluationRequest(
                device_id="sensor-01",
                capability_id="temperature-scan",
                execution_mode="simulation",
                target=GeofencePoint(1, 1, 0),
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "geofence.workspace.missing.deny")

    def test_denies_when_point_exits_workspace_boundary(self) -> None:
        decision = self.engine.evaluate(
            GeofenceEvaluationRequest(
                device_id="arm-01",
                capability_id="arm-move",
                execution_mode="simulation",
                target=GeofencePoint(11, 5, 2),
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "geofence.workspace.boundary.deny")

    def test_denies_when_intersecting_hard_no_go_zone(self) -> None:
        self.engine.register_no_go_zone(
            zone_id="core-forbidden",
            min_x=4,
            max_x=6,
            min_y=4,
            max_y=6,
            min_z=0,
            max_z=3,
            enforcement="deny",
        )

        decision = self.engine.evaluate(
            GeofenceEvaluationRequest(
                device_id="arm-01",
                capability_id="arm-move",
                execution_mode="live",
                target=GeofencePoint(5, 5, 1),
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "geofence.zone.deny")
        self.assertEqual(decision.violated_zone_ids, ("core-forbidden",))

    def test_requires_approval_for_soft_zone(self) -> None:
        self.engine.register_no_go_zone(
            zone_id="maintenance-lane",
            min_x=3,
            max_x=7,
            min_y=3,
            max_y=7,
            min_z=0,
            max_z=3,
            enforcement="require_approval",
        )

        decision = self.engine.evaluate(
            GeofenceEvaluationRequest(
                device_id="arm-01",
                capability_id="arm-move",
                execution_mode="live",
                target=GeofencePoint(5, 5, 1),
            )
        )

        self.assertEqual(decision.decision, "require_approval")
        self.assertEqual(decision.rule_id, "geofence.zone.require_approval")
        self.assertIn("geofence_override_required", decision.required_controls)

    def test_live_only_zone_does_not_block_simulation(self) -> None:
        self.engine.register_no_go_zone(
            zone_id="live-protect",
            min_x=2,
            max_x=8,
            min_y=2,
            max_y=8,
            min_z=0,
            max_z=4,
            enforcement="deny",
            active_modes=["live"],
        )

        decision = self.engine.evaluate(
            GeofenceEvaluationRequest(
                device_id="arm-01",
                capability_id="arm-move",
                execution_mode="simulation",
                target=GeofencePoint(5, 5, 1),
            )
        )

        self.assertEqual(decision.decision, "allow")
        self.assertEqual(decision.rule_id, "geofence.allow")

    def test_no_motion_request_is_allowed(self) -> None:
        decision = self.engine.evaluate(
            GeofenceEvaluationRequest(
                device_id="sensor-01",
                capability_id="temperature-scan",
                execution_mode="live",
            )
        )

        self.assertEqual(decision.decision, "allow")
        self.assertEqual(decision.rule_id, "geofence.allow.no_motion")


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
                "metadata": {"units": "celsius"},
            },
            {
                "capability_id": "arm-move",
                "capability_type": "actuator",
                "command": "move_arm",
                "risk_tier": "high",
                "requires_sandbox_approval": True,
                "simulation_supported": True,
                "safety_tags": ["motion", "pinch-hazard"],
                "metadata": {"axis_count": 6},
            },
        ],
        "metadata": {"site": "lab-a"},
    }


if __name__ == "__main__":
    unittest.main()
