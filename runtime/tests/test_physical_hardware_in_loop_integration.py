"""Integration tests for P9-T10 hardware-in-the-loop physical workflows."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.orchestration import OperationalEventBus
from runtime.control_plane import (
    GeofenceEvaluationRequest,
    GeofencePoint,
    PhysicalConnectorSDK,
    PhysicalDeviceRegistry,
    PhysicalEmergencyStopError,
    PhysicalEmergencyStopManager,
    PhysicalGeofenceEngine,
    PhysicalInterlockRequest,
    PhysicalManualTakeoverError,
    PhysicalManualTakeoverManager,
    PhysicalMissionTemplatePlanner,
    PhysicalPlanStep,
    PhysicalSafetyInterlockEngine,
    PhysicalSimulationHarness,
    PhysicalTelemetryIngestionManager,
    PhysicalTelemetryIngestionRequest,
)


class HardwareInLoopConnector:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

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
                "payload": dict(payload),
                "simulation": simulation,
                "identity": identity,
            }
        )
        return {
            "status": "ok",
            "capability": capability.capability_id,
            "mode": "simulation" if simulation else "live",
        }


class PhysicalHardwareInLoopIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = HardwareInLoopConnector()
        self.sdk = PhysicalConnectorSDK()
        self.sdk.register_connector(manifest=_base_manifest(), connector=self.connector)

        self.registry = PhysicalDeviceRegistry(self.sdk)
        self.registry.register_device(
            device_id="arm-01",
            connector_id="warehouse-robotics",
            display_name="Arm",
            trust_level="high",
            allowed_capability_ids=["arm-move", "arm-emergency-stop"],
        )
        self.registry.register_device(
            device_id="sensor-01",
            connector_id="warehouse-robotics",
            display_name="Sensor",
            trust_level="high",
            allowed_capability_ids=["temperature-scan"],
        )

        self.event_bus = OperationalEventBus()
        self.event_bus.subscribe(
            subscriber_id="ops",
            event_patterns=["physical.*"],
            min_severity="info",
        )

        self.emergency_stop = PhysicalEmergencyStopManager(self.sdk, self.registry)
        self.telemetry = PhysicalTelemetryIngestionManager(
            self.registry,
            event_bus=self.event_bus,
            emergency_stop_manager=self.emergency_stop,
        )
        self.takeover = PhysicalManualTakeoverManager(
            telemetry_ingestion=self.telemetry,
            emergency_stop_manager=self.emergency_stop,
            event_bus=self.event_bus,
        )

        self.harness = PhysicalSimulationHarness(self.sdk, self.registry)
        self.interlock = PhysicalSafetyInterlockEngine(self.registry)
        self.geofence = PhysicalGeofenceEngine(self.registry)
        self.geofence.set_device_workspace(
            device_id="arm-01",
            min_x=0,
            max_x=10,
            min_y=0,
            max_y=10,
            min_z=0,
            max_z=5,
        )

        self.templates = PhysicalMissionTemplatePlanner(self.registry)
        self.templates.register_template(
            template_id="hil-inspection",
            name="HIL Inspection",
            approved=True,
            steps=[
                {
                    "step_id": "move-arm",
                    "device_binding": "arm_device",
                    "capability_id": "arm-move",
                    "payload_template": {
                        "target": "{target_bin}",
                        "mission": "{mission_id}",
                    },
                },
                {
                    "step_id": "scan-temp",
                    "device_binding": "sensor_device",
                    "capability_id": "temperature-scan",
                    "payload_template": {
                        "zone": "{scan_zone}",
                    },
                },
            ],
        )

    def test_hardware_in_loop_simulation_to_live_promotion(self) -> None:
        plan = self.templates.render_plan(
            "hil-inspection",
            mission_id="mission-alpha",
            execution_mode="live",
            bindings={
                "arm_device": "arm-01",
                "sensor_device": "sensor-01",
                "target_bin": "bin-7",
                "scan_zone": "A1",
            },
        )

        arm_interlock = self.interlock.evaluate(
            PhysicalInterlockRequest(
                device_id="arm-01",
                capability_id="arm-move",
                execution_mode="live",
                operator_role="authorized_operator",
                sandbox_approved=True,
            )
        )
        self.assertEqual(arm_interlock.decision, "require_approval")
        self.assertIn("supervisor_ack_required", arm_interlock.required_controls)

        geofence_decision = self.geofence.evaluate(
            GeofenceEvaluationRequest(
                device_id="arm-01",
                capability_id="arm-move",
                execution_mode="live",
                target=GeofencePoint(6, 4, 2),
            )
        )
        self.assertEqual(geofence_decision.decision, "allow")

        steps = [
            PhysicalPlanStep(
                device_id=step.device_id,
                capability_id=step.capability_id,
                payload=step.payload,
            )
            for step in plan.steps
        ]

        simulated = self.harness.simulate_plan(plan.plan_id, steps, default_identity="hil-rig")
        live = self.harness.execute_live_plan(
            plan.plan_id,
            steps,
            sandbox_approved=True,
            default_identity="hil-rig",
        )

        self.assertTrue(simulated.ready_for_live)
        self.assertEqual(simulated.failed, 0)
        self.assertEqual(live.failed, 0)
        self.assertEqual(live.succeeded, len(steps))
        self.assertEqual(len(self.connector.calls), len(steps) * 2)
        self.assertEqual(sum(1 for call in self.connector.calls if call["simulation"]), len(steps))
        self.assertTrue(all(call["identity"] == "hil-rig" for call in self.connector.calls))

    def test_manual_takeover_under_active_mission_requires_override(self) -> None:
        self._ingest_arm_sample("mission-bravo", position_x=2.5, motor_temp=42.0)

        self.takeover.activate_takeover(
            mission_id="mission-bravo",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="hardware inspection",
        )

        with self.assertRaises(PhysicalManualTakeoverError):
            self.takeover.assert_can_execute(
                mission_id="mission-bravo",
                device_id="arm-01",
                capability_id="arm-move",
                required_controls=["supervisor_ack_required"],
            )

        override = self.takeover.grant_override(
            mission_id="mission-bravo",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="single controlled move",
            device_id="arm-01",
            capability_id="arm-move",
            required_controls=["supervisor_ack_required"],
            single_use=True,
            expires_in_seconds=120,
        )

        allowed = self.takeover.assert_can_execute(
            mission_id="mission-bravo",
            device_id="arm-01",
            capability_id="arm-move",
            required_controls=["supervisor_ack_required"],
        )
        execution = self.sdk.execute(
            "warehouse-robotics",
            "arm-move",
            payload={"target": "bin-5"},
            simulation=False,
            sandbox_approved=True,
            identity="op-1",
        )

        self.assertEqual(allowed.override_id, override.override_id)
        self.assertIsNotNone(allowed.consumed_at)
        self.assertEqual(execution.result["status"], "ok")

        with self.assertRaises(PhysicalManualTakeoverError):
            self.takeover.assert_can_execute(
                mission_id="mission-bravo",
                device_id="arm-01",
                capability_id="arm-move",
                required_controls=["supervisor_ack_required"],
            )

    def test_emergency_stop_propagates_and_forces_emergency_state(self) -> None:
        self._ingest_arm_sample("mission-charlie", position_x=1.0, motor_temp=41.0)
        self.takeover.activate_takeover(
            mission_id="mission-charlie",
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="manual guard",
        )

        stop_event = self.emergency_stop.activate(reason="facility_alarm", actor="safety-system")

        with self.assertRaises(PhysicalEmergencyStopError):
            self.emergency_stop.assert_can_execute()

        with self.assertRaises(PhysicalManualTakeoverError):
            self.takeover.grant_override(
                mission_id="mission-charlie",
                operator_id="op-1",
                operator_role="authorized_operator",
                reason="override under stop",
            )

        self._ingest_arm_sample("mission-charlie", position_x=1.5, motor_temp=43.0)
        mission_state = self.telemetry.get_mission_state("mission-charlie")

        events = self.event_bus.poll_subscriber("ops", include_acknowledged=True, limit=50)
        event_types = [event.event_type for event in events.events]

        self.assertEqual(stop_event.state, "active")
        self.assertEqual(stop_event.dispatched, 1)
        self.assertEqual(stop_event.skipped, 1)
        self.assertEqual(mission_state.state, "emergency_stop")
        self.assertIn("physical.takeover.activated", event_types)
        self.assertIn("physical.telemetry.mission.emergency_stop", event_types)

    def _ingest_arm_sample(self, mission_id: str, *, position_x: float, motor_temp: float) -> None:
        self.telemetry.ingest(
            PhysicalTelemetryIngestionRequest(
                mission_id=mission_id,
                device_id="arm-01",
                capability_id="arm-move",
                telemetry={
                    "position_x": position_x,
                    "motor_temp": motor_temp,
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
                "capability_id": "temperature-scan",
                "capability_type": "sensor",
                "command": "read_temperature",
                "risk_tier": "low",
                "requires_sandbox_approval": False,
                "simulation_supported": True,
                "telemetry_fields": ["temperature_c"],
                "safety_tags": ["read-only"],
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
