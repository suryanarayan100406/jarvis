"""Failure-mode tests for P9-T11 sensor loss and actuator faults."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.orchestration import OperationalEventBus
from runtime.control_plane import (
    PhysicalConnectorSDK,
    PhysicalDeviceRegistry,
    PhysicalPlanStep,
    PhysicalSimulationHarness,
    PhysicalSimulationHarnessError,
    PhysicalTelemetryIngestionManager,
    PhysicalTelemetryIngestionRequest,
)


class FaultInjectingPhysicalConnector:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_live_capability_ids: set[str] = set()

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

        if payload.get("inject_fault") == "actuator_stall" and capability.capability_id == "arm-move":
            raise RuntimeError("actuator fault: stall")

        if not simulation and capability.capability_id in self.fail_live_capability_ids:
            raise RuntimeError("actuator fault: overcurrent")

        return {
            "status": "ok",
            "mode": "simulation" if simulation else "live",
            "capability": capability.capability_id,
        }


class PhysicalFailureModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = FaultInjectingPhysicalConnector()
        self.sdk = PhysicalConnectorSDK()
        self.sdk.register_connector(manifest=_base_manifest(), connector=self.connector)

        self.registry = PhysicalDeviceRegistry(self.sdk)
        self.registry.register_device(
            device_id="sensor-01",
            connector_id="warehouse-robotics",
            display_name="Thermal Sensor",
            trust_level="high",
            allowed_capability_ids=["temperature-scan"],
        )
        self.registry.register_device(
            device_id="arm-01",
            connector_id="warehouse-robotics",
            display_name="Arm",
            trust_level="high",
            allowed_capability_ids=["arm-move"],
        )

        self.harness = PhysicalSimulationHarness(self.sdk, self.registry)

        self.event_bus = OperationalEventBus()
        self.event_bus.subscribe(
            subscriber_id="ops",
            event_patterns=["physical.telemetry.*"],
            min_severity="info",
        )
        self.telemetry = PhysicalTelemetryIngestionManager(
            self.registry,
            event_bus=self.event_bus,
        )

    def test_sensor_loss_demotes_nominal_signal_to_degraded(self) -> None:
        self.telemetry.ingest(
            PhysicalTelemetryIngestionRequest(
                mission_id="mission-sensor-loss",
                device_id="sensor-01",
                capability_id="temperature-scan",
                telemetry={"temperature_c": 22.4, "signal_quality": 0.98},
                status="nominal",
                execution_mode="live",
            )
        )

        degraded = self.telemetry.ingest(
            PhysicalTelemetryIngestionRequest(
                mission_id="mission-sensor-loss",
                device_id="sensor-01",
                capability_id="temperature-scan",
                telemetry={},
                status="nominal",
                execution_mode="live",
            )
        )

        state = self.telemetry.get_mission_state("mission-sensor-loss")
        events = self.event_bus.poll_subscriber("ops", include_acknowledged=True, limit=20)
        event_types = [event.event_type for event in events.events]

        self.assertEqual(degraded.status, "degraded")
        self.assertEqual(state.state, "degraded")
        self.assertEqual(state.degraded_devices, 1)
        self.assertIn("physical.telemetry.missing_fields", event_types)
        self.assertIn("physical.telemetry.device.degraded", event_types)

    def test_actuator_fault_during_simulation_blocks_live_promotion(self) -> None:
        steps = self._mission_steps(actuator_fault=True)

        simulated = self.harness.simulate_plan(
            "mission-actuator-fault",
            steps,
            fail_fast=True,
        )

        self.assertFalse(simulated.ready_for_live)
        self.assertEqual(simulated.succeeded, 1)
        self.assertEqual(simulated.failed, 1)
        self.assertEqual(simulated.skipped, 1)
        self.assertIn("actuator fault", simulated.results[1].error or "")

        with self.assertRaises(PhysicalSimulationHarnessError):
            self.harness.execute_live_plan(
                "mission-actuator-fault",
                steps,
                sandbox_approved=True,
            )

    def test_live_actuator_fault_and_faulted_telemetry_mark_faulted_state(self) -> None:
        steps = self._mission_steps(actuator_fault=False)

        simulated = self.harness.simulate_plan("mission-live-fault", steps)
        self.assertTrue(simulated.ready_for_live)

        self.connector.fail_live_capability_ids.add("arm-move")
        live = self.harness.execute_live_plan(
            "mission-live-fault",
            steps,
            sandbox_approved=True,
            fail_fast=True,
        )

        self.assertEqual(live.succeeded, 1)
        self.assertEqual(live.failed, 1)
        self.assertEqual(live.skipped, 1)
        self.assertIn("actuator fault", live.results[1].error or "")

        self.telemetry.ingest(
            PhysicalTelemetryIngestionRequest(
                mission_id="mission-live-fault",
                device_id="arm-01",
                capability_id="arm-move",
                telemetry={"position_x": 0.9, "motor_temp": 101.2},
                status="faulted",
                execution_mode="live",
            )
        )

        state = self.telemetry.get_mission_state("mission-live-fault")
        events = self.event_bus.poll_subscriber("ops", include_acknowledged=True, limit=20)
        event_types = [event.event_type for event in events.events]

        self.assertEqual(state.state, "faulted")
        self.assertEqual(state.faulted_devices, 1)
        self.assertIn("physical.telemetry.device.fault", event_types)

    @staticmethod
    def _mission_steps(*, actuator_fault: bool) -> list[PhysicalPlanStep]:
        actuator_payload: dict[str, Any] = {"target": "bin-7"}
        if actuator_fault:
            actuator_payload["inject_fault"] = "actuator_stall"

        return [
            PhysicalPlanStep(
                device_id="sensor-01",
                capability_id="temperature-scan",
                payload={"zone": "A1"},
            ),
            PhysicalPlanStep(
                device_id="arm-01",
                capability_id="arm-move",
                payload=actuator_payload,
            ),
            PhysicalPlanStep(
                device_id="sensor-01",
                capability_id="temperature-scan",
                payload={"zone": "A2"},
            ),
        ]



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
                "telemetry_fields": ["temperature_c", "signal_quality"],
                "safety_tags": ["read-only"],
                "metadata": {},
            },
            {
                "capability_id": "arm-move",
                "capability_type": "actuator",
                "command": "move_arm",
                "risk_tier": "high",
                "requires_sandbox_approval": True,
                "simulation_supported": True,
                "telemetry_fields": ["position_x", "motor_temp"],
                "safety_tags": ["motion"],
                "metadata": {},
            },
        ],
        "metadata": {"site": "lab-a"},
    }


if __name__ == "__main__":
    unittest.main()
