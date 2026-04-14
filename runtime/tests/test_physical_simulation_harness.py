"""Tests for P9-T3 simulation harness for motion and actuation plans."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control_plane import (
    PhysicalConnectorSDK,
    PhysicalDeviceRegistry,
    PhysicalSimulationHarness,
    PhysicalSimulationHarnessError,
    PhysicalPlanStep,
)


class RecordingPhysicalConnector:
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

        if payload.get("force_error"):
            raise RuntimeError("simulated connector failure")

        return {
            "status": "ok",
            "mode": "simulation" if simulation else "live",
            "capability": capability.capability_id,
        }


class PhysicalSimulationHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = RecordingPhysicalConnector()
        self.sdk = PhysicalConnectorSDK()
        self.sdk.register_connector(
            manifest=_base_manifest(),
            connector=self.connector,
        )

        self.registry = PhysicalDeviceRegistry(self.sdk)
        self.registry.register_device(
            device_id="sensor-01",
            connector_id="warehouse-robotics",
            display_name="Temp Sensor",
            trust_level="high",
            allowed_capability_ids=["temperature-scan"],
            trust_tags=["monitoring"],
        )
        self.registry.register_device(
            device_id="arm-01",
            connector_id="warehouse-robotics",
            display_name="Arm",
            trust_level="medium",
            allowed_capability_ids=["arm-move"],
            trust_tags=["production"],
        )

        self.harness = PhysicalSimulationHarness(self.sdk, self.registry)

    def test_simulate_plan_succeeds_and_marks_ready_for_live(self) -> None:
        result = self.harness.simulate_plan(
            "mission-alpha",
            _base_plan_steps(),
            default_identity="ops-bot",
        )

        self.assertEqual(result.mode, "simulation")
        self.assertEqual(result.succeeded, 2)
        self.assertEqual(result.failed, 0)
        self.assertTrue(result.ready_for_live)
        self.assertTrue(result.requires_sandbox_approval)
        self.assertEqual(len(self.connector.calls), 2)
        self.assertEqual(self.connector.calls[0]["identity"], "ops-bot")
        self.assertTrue(self.connector.calls[0]["simulation"])

    def test_execute_live_plan_requires_prior_simulation(self) -> None:
        with self.assertRaises(PhysicalSimulationHarnessError):
            self.harness.execute_live_plan(
                "mission-alpha",
                _base_plan_steps(),
                sandbox_approved=True,
            )

    def test_execute_live_plan_requires_sandbox_approval_for_actuation(self) -> None:
        self.harness.simulate_plan("mission-alpha", _base_plan_steps())

        with self.assertRaises(PhysicalSimulationHarnessError):
            self.harness.execute_live_plan(
                "mission-alpha",
                _base_plan_steps(),
                sandbox_approved=False,
            )

    def test_execute_live_plan_rejects_when_plan_changes_after_simulation(self) -> None:
        self.harness.simulate_plan("mission-alpha", _base_plan_steps())

        changed_steps = _base_plan_steps()
        changed_steps[1] = PhysicalPlanStep(
            device_id="arm-01",
            capability_id="arm-move",
            payload={"target": "bin-9"},
        )

        with self.assertRaises(PhysicalSimulationHarnessError):
            self.harness.execute_live_plan(
                "mission-alpha",
                changed_steps,
                sandbox_approved=True,
            )

    def test_execute_live_plan_succeeds_after_simulation_and_approval(self) -> None:
        self.harness.simulate_plan("mission-alpha", _base_plan_steps())

        live_result = self.harness.execute_live_plan(
            "mission-alpha",
            _base_plan_steps(),
            sandbox_approved=True,
            default_identity="ops-live",
        )

        self.assertEqual(live_result.mode, "live")
        self.assertEqual(live_result.succeeded, 2)
        self.assertEqual(live_result.failed, 0)
        self.assertFalse(live_result.ready_for_live)
        self.assertEqual(len(self.connector.calls), 4)
        self.assertFalse(self.connector.calls[2]["simulation"])
        self.assertEqual(self.connector.calls[2]["identity"], "ops-live")

    def test_fail_fast_marks_remaining_steps_skipped(self) -> None:
        failing = [
            PhysicalPlanStep(
                device_id="sensor-01",
                capability_id="temperature-scan",
                payload={"force_error": True},
            ),
            PhysicalPlanStep(
                device_id="arm-01",
                capability_id="arm-move",
                payload={"target": "bin-7"},
            ),
        ]

        result = self.harness.simulate_plan("mission-fail-fast", failing, fail_fast=True)

        self.assertEqual(result.failed, 1)
        self.assertEqual(result.skipped, 1)
        self.assertFalse(result.ready_for_live)
        self.assertEqual(result.results[1].status, "skipped")

    def test_disabled_device_fails_plan_step(self) -> None:
        self.registry.update_device("arm-01", enabled=False)

        result = self.harness.simulate_plan("mission-disabled", _base_plan_steps())

        self.assertEqual(result.failed, 1)
        self.assertFalse(result.ready_for_live)
        self.assertIn("disabled", result.results[1].error or "")



def _base_plan_steps() -> list[PhysicalPlanStep]:
    return [
        PhysicalPlanStep(
            device_id="sensor-01",
            capability_id="temperature-scan",
            payload={"zone": "A1"},
        ),
        PhysicalPlanStep(
            device_id="arm-01",
            capability_id="arm-move",
            payload={"target": "bin-7"},
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
