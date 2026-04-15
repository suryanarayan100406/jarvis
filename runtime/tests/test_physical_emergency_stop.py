"""Tests for P9-T6 emergency-stop propagation to physical connectors."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control import KillSwitchController
from runtime.control_plane import (
    PhysicalConnectorSDK,
    PhysicalDeviceRegistry,
    PhysicalEmergencyStopError,
    PhysicalEmergencyStopManager,
)


class RecordingPhysicalConnector:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_device_ids: set[str] = set()

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

        if payload.get("device_id") in self.fail_device_ids:
            raise RuntimeError("device channel failure")

        return {
            "status": "ok",
            "capability": capability.capability_id,
            "mode": "simulation" if simulation else "live",
        }


class PhysicalEmergencyStopManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = RecordingPhysicalConnector()
        self.sdk = PhysicalConnectorSDK()
        self.sdk.register_connector(manifest=_base_manifest(), connector=self.connector)

        self.registry = PhysicalDeviceRegistry(self.sdk)
        self.registry.register_device(
            device_id="arm-01",
            connector_id="warehouse-robotics",
            display_name="Arm 01",
            trust_level="high",
            allowed_capability_ids=["arm-emergency-stop", "arm-move"],
        )
        self.registry.register_device(
            device_id="arm-02",
            connector_id="warehouse-robotics",
            display_name="Arm 02",
            trust_level="high",
            allowed_capability_ids=["arm-emergency-stop", "arm-move"],
        )
        self.registry.register_device(
            device_id="sensor-01",
            connector_id="warehouse-robotics",
            display_name="Sensor",
            trust_level="high",
            allowed_capability_ids=["temperature-scan"],
        )

        self.manager = PhysicalEmergencyStopManager(self.sdk, self.registry)

    def test_activate_propagates_to_devices_with_emergency_capability(self) -> None:
        event = self.manager.activate(reason="operator_emergency", actor="boss")

        self.assertEqual(event.state, "active")
        self.assertEqual(event.dispatched, 2)
        self.assertEqual(event.skipped, 1)
        self.assertEqual(event.failed, 0)
        self.assertTrue(self.manager.is_active())

        dispatched_capabilities = [
            call["capability_id"]
            for call in self.connector.calls
        ]
        self.assertEqual(dispatched_capabilities.count("arm-emergency-stop"), 2)
        self.assertTrue(all(call["simulation"] is False for call in self.connector.calls))
        self.assertTrue(all(call["payload"]["emergency_stop"] for call in self.connector.calls))

    def test_activation_is_idempotent_while_active(self) -> None:
        first = self.manager.activate(reason="operator_emergency", actor="boss")
        second = self.manager.activate(reason="operator_emergency", actor="boss")

        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(len(self.connector.calls), 2)

    def test_reset_clears_active_state(self) -> None:
        self.manager.activate(reason="operator_emergency", actor="boss")
        reset = self.manager.reset(reason="resolved", actor="boss")

        self.assertFalse(self.manager.is_active())
        self.assertEqual(reset.state, "inactive")
        self.manager.assert_can_execute()

    def test_assert_can_execute_raises_when_active(self) -> None:
        self.manager.activate(reason="operator_emergency", actor="boss")

        with self.assertRaises(PhysicalEmergencyStopError):
            self.manager.assert_can_execute()

    def test_dispatch_records_failures_and_continues(self) -> None:
        original_execute = self.sdk.execute

        def failing_execute(
            connector_id: str,
            capability_id: str,
            payload: dict[str, Any] | None = None,
            *,
            simulation: bool = True,
            sandbox_approved: bool = False,
            identity: str | None = None,
        ):
            if payload and payload.get("device_id") == "arm-02":
                raise RuntimeError("device channel failure")
            return original_execute(
                connector_id,
                capability_id,
                payload=payload,
                simulation=simulation,
                sandbox_approved=sandbox_approved,
                identity=identity,
            )

        self.sdk.execute = failing_execute  # type: ignore[assignment]

        event = self.manager.activate(reason="operator_emergency", actor="boss")

        self.assertEqual(event.dispatched, 1)
        self.assertEqual(event.failed, 1)
        self.assertEqual(event.skipped, 1)

    def test_kill_switch_hook_triggers_propagation(self) -> None:
        kill_switch = KillSwitchController()
        self.manager.register_kill_switch_hook(kill_switch)

        kill_event = kill_switch.activate(reason="facility_alarm", actor="system")

        self.assertIn("physical_emergency_stop", kill_event.triggered_hooks)
        self.assertTrue(self.manager.is_active())
        self.assertEqual(self.manager.history[-1].source, "kill_switch_hook")



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
                "safety_tags": ["read-only"],
                "telemetry_fields": ["temperature_c"],
                "metadata": {},
            },
            {
                "capability_id": "arm-move",
                "capability_type": "actuator",
                "command": "move_arm",
                "risk_tier": "high",
                "requires_sandbox_approval": True,
                "simulation_supported": True,
                "safety_tags": ["motion"],
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
