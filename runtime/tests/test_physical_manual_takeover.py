"""Tests for P9-T9 manual takeover and override workflows."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.orchestration import OperationalEventBus
from runtime.control_plane import (
    PhysicalConnectorSDK,
    PhysicalDeviceRegistry,
    PhysicalEmergencyStopManager,
    PhysicalManualTakeoverError,
    PhysicalManualTakeoverManager,
    PhysicalTelemetryIngestionManager,
    PhysicalTelemetryIngestionRequest,
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
            "capability": capability.capability_id,
            "simulation": simulation,
        }


class PhysicalManualTakeoverManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = PhysicalConnectorSDK()
        self.sdk.register_connector(manifest=_base_manifest(), connector=RecordingPhysicalConnector())

        self.registry = PhysicalDeviceRegistry(self.sdk)
        self.registry.register_device(
            device_id="arm-01",
            connector_id="warehouse-robotics",
            display_name="Arm",
            trust_level="high",
            allowed_capability_ids=["arm-move", "arm-emergency-stop"],
        )

        self.event_bus = OperationalEventBus()
        self.event_bus.subscribe(
            subscriber_id="ops",
            event_patterns=["physical.takeover.*"],
            min_severity="info",
        )

        self.emergency_stop = PhysicalEmergencyStopManager(self.sdk, self.registry)
        self.ingestion = PhysicalTelemetryIngestionManager(
            self.registry,
            event_bus=self.event_bus,
            emergency_stop_manager=self.emergency_stop,
        )

        self.manager = PhysicalManualTakeoverManager(
            telemetry_ingestion=self.ingestion,
            emergency_stop_manager=self.emergency_stop,
            event_bus=self.event_bus,
        )

    def test_activate_takeover_requires_live_mission_state(self) -> None:
        with self.assertRaises(PhysicalManualTakeoverError):
            self.manager.activate_takeover(
                mission_id="mission-alpha",
                operator_id="op-1",
                operator_role="authorized_operator",
                reason="manual review",
            )

        self._ingest_nominal_sample("mission-alpha")
        session = self.manager.activate_takeover(
            mission_id="mission-alpha",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="manual review",
        )

        events = self.event_bus.poll_subscriber("ops", include_acknowledged=True)
        event_types = [event.event_type for event in events.events]

        self.assertEqual(session.state, "active")
        self.assertEqual(session.mission_state, "active")
        self.assertTrue(self.manager.is_takeover_active("mission-alpha"))
        self.assertIn("physical.takeover.activated", event_types)

    def test_activate_takeover_rejects_unsupported_operator_role(self) -> None:
        self._ingest_nominal_sample("mission-alpha")

        with self.assertRaises(PhysicalManualTakeoverError):
            self.manager.activate_takeover(
                mission_id="mission-alpha",
                operator_id="op-1",
                operator_role="viewer",
                reason="manual review",
            )

    def test_assert_can_execute_blocks_without_override(self) -> None:
        self._ingest_nominal_sample("mission-alpha")
        self.manager.activate_takeover(
            mission_id="mission-alpha",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="manual handling",
        )

        with self.assertRaises(PhysicalManualTakeoverError):
            self.manager.assert_can_execute(
                mission_id="mission-alpha",
                device_id="arm-01",
                capability_id="arm-move",
                required_controls=["supervisor_ack_required"],
            )

    def test_single_use_override_is_consumed(self) -> None:
        self._ingest_nominal_sample("mission-alpha")
        self.manager.activate_takeover(
            mission_id="mission-alpha",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="manual handling",
        )

        grant = self.manager.grant_override(
            mission_id="mission-alpha",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="allow one controlled command",
            device_id="arm-01",
            capability_id="arm-move",
            required_controls=["supervisor_ack_required"],
            single_use=True,
            expires_in_seconds=120,
        )

        allowed = self.manager.assert_can_execute(
            mission_id="mission-alpha",
            device_id="arm-01",
            capability_id="arm-move",
            required_controls=["supervisor_ack_required"],
        )

        self.assertEqual(allowed.override_id, grant.override_id)
        self.assertIsNotNone(allowed.consumed_at)

        with self.assertRaises(PhysicalManualTakeoverError):
            self.manager.assert_can_execute(
                mission_id="mission-alpha",
                device_id="arm-01",
                capability_id="arm-move",
                required_controls=["supervisor_ack_required"],
            )

    def test_multi_use_override_can_be_reused(self) -> None:
        self._ingest_nominal_sample("mission-alpha")
        self.manager.activate_takeover(
            mission_id="mission-alpha",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="manual handling",
        )

        grant = self.manager.grant_override(
            mission_id="mission-alpha",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="keep command path available",
            single_use=False,
            expires_in_seconds=None,
        )

        first = self.manager.assert_can_execute(
            mission_id="mission-alpha",
            device_id="arm-01",
            capability_id="arm-move",
        )
        second = self.manager.assert_can_execute(
            mission_id="mission-alpha",
            device_id="arm-01",
            capability_id="arm-move",
        )

        self.assertEqual(first.override_id, grant.override_id)
        self.assertEqual(second.override_id, grant.override_id)
        self.assertIsNone(second.consumed_at)

    def test_grant_override_blocked_when_emergency_stop_active(self) -> None:
        self._ingest_nominal_sample("mission-alpha")
        self.manager.activate_takeover(
            mission_id="mission-alpha",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="manual handling",
        )
        self.emergency_stop.activate(reason="facility_alarm", actor="system")

        with self.assertRaises(PhysicalManualTakeoverError):
            self.manager.grant_override(
                mission_id="mission-alpha",
                operator_id="op-1",
                operator_role="authorized_operator",
                reason="attempt override under e-stop",
            )

    def test_release_takeover_revokes_pending_overrides_and_unblocks_execution(self) -> None:
        self._ingest_nominal_sample("mission-alpha")
        self.manager.activate_takeover(
            mission_id="mission-alpha",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="manual handling",
        )
        self.manager.grant_override(
            mission_id="mission-alpha",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="keep command path available",
            single_use=False,
            expires_in_seconds=None,
        )

        released = self.manager.release_takeover(
            mission_id="mission-alpha",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="handoff complete",
        )

        overrides = self.manager.list_overrides("mission-alpha", include_inactive=True)
        unblock = self.manager.assert_can_execute(
            mission_id="mission-alpha",
            device_id="arm-01",
            capability_id="arm-move",
        )

        self.assertEqual(released.state, "released")
        self.assertFalse(self.manager.is_takeover_active("mission-alpha"))
        self.assertEqual(len(overrides), 1)
        self.assertIsNotNone(overrides[0].revoked_at)
        self.assertIsNone(unblock)

    def test_non_owner_cannot_release_without_privileged_role(self) -> None:
        self._ingest_nominal_sample("mission-alpha")
        self.manager.activate_takeover(
            mission_id="mission-alpha",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="manual handling",
        )

        with self.assertRaises(PhysicalManualTakeoverError):
            self.manager.release_takeover(
                mission_id="mission-alpha",
                operator_id="op-2",
                operator_role="authorized_operator",
                reason="attempt release",
            )

        released = self.manager.release_takeover(
            mission_id="mission-alpha",
            operator_id="boss",
            operator_role="primary_user",
            reason="supervisor handoff",
        )
        self.assertEqual(released.state, "released")

    def _ingest_nominal_sample(self, mission_id: str) -> None:
        self.ingestion.ingest(
            PhysicalTelemetryIngestionRequest(
                mission_id=mission_id,
                device_id="arm-01",
                capability_id="arm-move",
                telemetry={
                    "position_x": 1.0,
                    "motor_temp": 40.0,
                },
                status="nominal",
                execution_mode="live",
            )
        )



def _base_manifest() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "connector_id": "warehouse-robotics",
        "provider": "Acme Robotics",
        "connector_kind": "robotics",
        "capabilities": [
            {
                "capability_id": "arm-move",
                "capability_type": "actuator",
                "command": "move_arm",
                "risk_tier": "high",
                "requires_sandbox_approval": True,
                "simulation_supported": True,
                "safety_tags": ["motion"],
                "telemetry_fields": ["position_x", "motor_temp"],
                "metadata": {},
            },
            {
                "capability_id": "arm-emergency-stop",
                "capability_type": "actuator",
                "command": "emergency_stop",
                "risk_tier": "critical",
                "requires_sandbox_approval": True,
                "simulation_supported": False,
                "safety_tags": ["emergency-stop"],
                "metadata": {},
            },
        ],
        "metadata": {"site": "lab-a"},
    }


if __name__ == "__main__":
    unittest.main()
