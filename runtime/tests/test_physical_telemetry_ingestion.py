"""Tests for P9-T7 live mission telemetry ingestion."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.orchestration import OperationalEventBus
from runtime.control_plane import (
    PhysicalConnectorSDK,
    PhysicalDeviceRegistry,
    PhysicalEmergencyStopManager,
    PhysicalTelemetryIngestionError,
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


class PhysicalTelemetryIngestionManagerTests(unittest.TestCase):
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
            event_patterns=["physical.telemetry.*"],
            min_severity="info",
        )

        self.emergency_stop = PhysicalEmergencyStopManager(self.sdk, self.registry)
        self.ingestion = PhysicalTelemetryIngestionManager(
            self.registry,
            event_bus=self.event_bus,
            emergency_stop_manager=self.emergency_stop,
            max_samples_per_mission=50,
        )

    def test_ingest_live_sample_updates_active_mission_state(self) -> None:
        sample = self.ingestion.ingest(
            PhysicalTelemetryIngestionRequest(
                mission_id="mission-alpha",
                device_id="arm-01",
                capability_id="arm-move",
                telemetry={"position_x": 1.5, "motor_temp": 42.0},
                status="nominal",
                execution_mode="live",
            )
        )

        state = self.ingestion.get_mission_state("mission-alpha")

        self.assertEqual(sample.sequence, 1)
        self.assertEqual(state.state, "active")
        self.assertEqual(state.total_samples, 1)
        self.assertEqual(state.nominal_devices, 1)

    def test_simulation_mode_telemetry_is_rejected(self) -> None:
        with self.assertRaises(PhysicalTelemetryIngestionError):
            self.ingestion.ingest(
                PhysicalTelemetryIngestionRequest(
                    mission_id="mission-alpha",
                    device_id="arm-01",
                    capability_id="arm-move",
                    telemetry={"position_x": 1.5, "motor_temp": 42.0},
                    execution_mode="simulation",
                )
            )

    def test_faulted_sample_sets_faulted_state_and_emits_critical_event(self) -> None:
        self.ingestion.ingest(
            PhysicalTelemetryIngestionRequest(
                mission_id="mission-bravo",
                device_id="arm-01",
                capability_id="arm-move",
                telemetry={"position_x": 2.0, "motor_temp": 99.0},
                status="faulted",
                execution_mode="live",
            )
        )

        state = self.ingestion.get_mission_state("mission-bravo")
        events = self.event_bus.poll_subscriber("ops", include_acknowledged=True, limit=20)
        event_types = [event.event_type for event in events.events]

        self.assertEqual(state.state, "faulted")
        self.assertIn("physical.telemetry.device.fault", event_types)

    def test_missing_expected_fields_demotes_nominal_to_degraded(self) -> None:
        sample = self.ingestion.ingest(
            PhysicalTelemetryIngestionRequest(
                mission_id="mission-charlie",
                device_id="arm-01",
                capability_id="arm-move",
                telemetry={"position_x": 3.0},
                status="nominal",
                execution_mode="live",
            )
        )

        state = self.ingestion.get_mission_state("mission-charlie")
        events = self.event_bus.poll_subscriber("ops", include_acknowledged=True, limit=20)
        event_types = [event.event_type for event in events.events]

        self.assertEqual(sample.status, "degraded")
        self.assertEqual(state.state, "degraded")
        self.assertIn("physical.telemetry.missing_fields", event_types)

    def test_emergency_stop_forces_emergency_state(self) -> None:
        self.emergency_stop.activate(reason="facility_alarm", actor="system")

        self.ingestion.ingest(
            PhysicalTelemetryIngestionRequest(
                mission_id="mission-delta",
                device_id="arm-01",
                capability_id="arm-move",
                telemetry={"position_x": 4.0, "motor_temp": 44.0},
                status="nominal",
                execution_mode="live",
            )
        )

        state = self.ingestion.get_mission_state("mission-delta")
        events = self.event_bus.poll_subscriber("ops", include_acknowledged=True, limit=20)
        event_types = [event.event_type for event in events.events]

        self.assertEqual(state.state, "emergency_stop")
        self.assertIn("physical.telemetry.mission.emergency_stop", event_types)

    def test_sequence_must_increase_per_device(self) -> None:
        self.ingestion.ingest(
            PhysicalTelemetryIngestionRequest(
                mission_id="mission-echo",
                device_id="arm-01",
                capability_id="arm-move",
                telemetry={"position_x": 1.0, "motor_temp": 41.0},
                execution_mode="live",
                sequence=5,
            )
        )

        with self.assertRaises(PhysicalTelemetryIngestionError):
            self.ingestion.ingest(
                PhysicalTelemetryIngestionRequest(
                    mission_id="mission-echo",
                    device_id="arm-01",
                    capability_id="arm-move",
                    telemetry={"position_x": 2.0, "motor_temp": 42.0},
                    execution_mode="live",
                    sequence=5,
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
