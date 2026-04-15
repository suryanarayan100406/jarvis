"""Compliance tests for P9-T12 mandatory simulation-before-live policy."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.orchestration import OperationalEventBus
from runtime.control_plane import (
    PhysicalConnectorSDK,
    PhysicalDeviceRegistry,
    PhysicalManualTakeoverManager,
    PhysicalMissionTemplatePlanner,
    PhysicalPlanStep,
    PhysicalSimulationHarness,
    PhysicalSimulationHarnessError,
    PhysicalTelemetryIngestionManager,
    PhysicalTelemetryIngestionRequest,
)


class CompliancePhysicalConnector:
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

        if payload.get("inject_fault") is True and capability.capability_id == "arm-move":
            raise RuntimeError("simulated actuator compliance fault")

        return {
            "status": "ok",
            "capability": capability.capability_id,
            "mode": "simulation" if simulation else "live",
        }


class PhysicalSimulationComplianceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = CompliancePhysicalConnector()
        self.sdk = PhysicalConnectorSDK()
        self.sdk.register_connector(manifest=_base_manifest(), connector=self.connector)

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

        self.harness = PhysicalSimulationHarness(self.sdk, self.registry)
        self.templates = PhysicalMissionTemplatePlanner(self.registry)
        self.templates.register_template(
            template_id="compliance-inspection",
            name="Compliance Inspection",
            approved=True,
            steps=[
                {
                    "step_id": "move-arm",
                    "device_binding": "arm_device",
                    "capability_id": "arm-move",
                    "payload_template": {
                        "target": "{target_bin}",
                    },
                },
                {
                    "step_id": "scan",
                    "device_binding": "sensor_device",
                    "capability_id": "temperature-scan",
                    "payload_template": {
                        "zone": "{scan_zone}",
                    },
                },
            ],
        )

        self.event_bus = OperationalEventBus()
        self.event_bus.subscribe(
            subscriber_id="ops",
            event_patterns=["physical.takeover.*", "physical.telemetry.*"],
            min_severity="info",
        )
        self.telemetry = PhysicalTelemetryIngestionManager(
            self.registry,
            event_bus=self.event_bus,
        )
        self.takeover = PhysicalManualTakeoverManager(
            telemetry_ingestion=self.telemetry,
            event_bus=self.event_bus,
        )

    def test_live_actuation_without_simulation_is_blocked_even_with_override(self) -> None:
        mission_id = "mission-compliance-override"
        rendered_plan, steps = self._render_plan_steps(mission_id)

        self.telemetry.ingest(
            PhysicalTelemetryIngestionRequest(
                mission_id=mission_id,
                device_id="arm-01",
                capability_id="arm-move",
                telemetry={
                    "position_x": 1.1,
                    "motor_temp": 39.5,
                },
                execution_mode="live",
            )
        )
        self.takeover.activate_takeover(
            mission_id=mission_id,
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="manual supervision",
        )
        self.takeover.grant_override(
            mission_id=mission_id,
            operator_id="op-1",
            operator_role="authorized_operator",
            reason="allow one controlled move",
            device_id="arm-01",
            capability_id="arm-move",
            required_controls=["simulation_approval_required", "supervisor_ack_required"],
            single_use=False,
            expires_in_seconds=None,
        )

        override = self.takeover.assert_can_execute(
            mission_id=mission_id,
            device_id="arm-01",
            capability_id="arm-move",
            required_controls=["simulation_approval_required"],
        )
        self.assertIsNotNone(override)

        with self.assertRaises(PhysicalSimulationHarnessError):
            self.harness.execute_live_plan(
                rendered_plan.plan_id,
                steps,
                sandbox_approved=True,
            )

    def test_simulation_token_is_single_use_for_live_promotion(self) -> None:
        rendered_plan, steps = self._render_plan_steps("mission-compliance-single-use")

        simulated = self.harness.simulate_plan(rendered_plan.plan_id, steps)
        first_live = self.harness.execute_live_plan(
            rendered_plan.plan_id,
            steps,
            sandbox_approved=True,
        )

        self.assertTrue(simulated.ready_for_live)
        self.assertEqual(first_live.failed, 0)

        with self.assertRaises(PhysicalSimulationHarnessError):
            self.harness.execute_live_plan(
                rendered_plan.plan_id,
                steps,
                sandbox_approved=True,
            )

    def test_plan_mutation_after_simulation_invalidates_approval(self) -> None:
        rendered_plan, steps = self._render_plan_steps("mission-compliance-mutation")

        self.harness.simulate_plan(rendered_plan.plan_id, steps)

        changed_steps = list(steps)
        changed_steps[0] = PhysicalPlanStep(
            device_id="arm-01",
            capability_id="arm-move",
            payload={"target": "bin-99"},
        )

        with self.assertRaises(PhysicalSimulationHarnessError):
            self.harness.execute_live_plan(
                rendered_plan.plan_id,
                changed_steps,
                sandbox_approved=True,
            )

    def test_failed_simulation_never_allows_live_execution(self) -> None:
        rendered_plan, steps = self._render_plan_steps("mission-compliance-failed-sim")

        failing_steps = list(steps)
        failing_steps[0] = PhysicalPlanStep(
            device_id="arm-01",
            capability_id="arm-move",
            payload={"target": "bin-7", "inject_fault": True},
        )

        simulated = self.harness.simulate_plan(
            rendered_plan.plan_id,
            failing_steps,
            fail_fast=True,
        )

        self.assertFalse(simulated.ready_for_live)
        self.assertEqual(simulated.failed, 1)

        with self.assertRaises(PhysicalSimulationHarnessError):
            self.harness.execute_live_plan(
                rendered_plan.plan_id,
                failing_steps,
                sandbox_approved=True,
            )

    def test_live_template_declares_simulation_approval_required_control(self) -> None:
        live_plan = self.templates.render_plan(
            "compliance-inspection",
            mission_id="mission-compliance-controls-live",
            execution_mode="live",
            bindings={
                "arm_device": "arm-01",
                "sensor_device": "sensor-01",
                "target_bin": "bin-7",
                "scan_zone": "A1",
            },
        )
        simulation_plan = self.templates.render_plan(
            "compliance-inspection",
            mission_id="mission-compliance-controls-sim",
            execution_mode="simulation",
            bindings={
                "arm_device": "arm-01",
                "sensor_device": "sensor-01",
                "target_bin": "bin-7",
                "scan_zone": "A1",
            },
        )

        self.assertIn("simulation_approval_required", live_plan.required_controls)
        self.assertNotIn("simulation_approval_required", simulation_plan.required_controls)

    def _render_plan_steps(self, mission_id: str) -> tuple[Any, list[PhysicalPlanStep]]:
        rendered_plan = self.templates.render_plan(
            "compliance-inspection",
            mission_id=mission_id,
            execution_mode="live",
            bindings={
                "arm_device": "arm-01",
                "sensor_device": "sensor-01",
                "target_bin": "bin-7",
                "scan_zone": "A1",
            },
        )

        steps = [
            PhysicalPlanStep(
                device_id=step.device_id,
                capability_id=step.capability_id,
                payload=step.payload,
            )
            for step in rendered_plan.steps
        ]
        return rendered_plan, steps



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
                "telemetry_fields": ["position_x", "motor_temp"],
                "safety_tags": ["motion"],
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
        ],
        "metadata": {"site": "lab-a"},
    }


if __name__ == "__main__":
    unittest.main()
