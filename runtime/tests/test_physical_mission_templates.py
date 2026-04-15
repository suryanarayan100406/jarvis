"""Tests for P9-T8 mission planner templates for approved physical tasks."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control_plane import (
    PhysicalConnectorSDK,
    PhysicalDeviceRegistry,
    PhysicalMissionTemplateError,
    PhysicalMissionTemplatePlanner,
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


class PhysicalMissionTemplatePlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = PhysicalConnectorSDK()
        self.sdk.register_connector(manifest=_base_manifest(), connector=RecordingPhysicalConnector())

        self.registry = PhysicalDeviceRegistry(self.sdk)
        self.registry.register_device(
            device_id="arm-01",
            connector_id="warehouse-robotics",
            display_name="Arm",
            trust_level="high",
            allowed_capability_ids=["arm-move", "arm-critical"],
        )
        self.registry.register_device(
            device_id="sensor-01",
            connector_id="warehouse-robotics",
            display_name="Sensor",
            trust_level="high",
            allowed_capability_ids=["temperature-scan"],
        )

        self.planner = PhysicalMissionTemplatePlanner(self.registry)

    def test_register_and_list_templates(self) -> None:
        self.planner.register_template(
            template_id="pick-and-scan",
            name="Pick and Scan",
            steps=_base_template_steps(),
            execution_modes=["simulation", "live"],
            approved=True,
        )
        self.planner.register_template(
            template_id="critical-maintenance",
            name="Critical Maintenance",
            steps=[
                {
                    "step_id": "critical",
                    "device_binding": "arm_device",
                    "capability_id": "arm-critical",
                    "payload_template": {"target": "{target_bin}"},
                }
            ],
            execution_modes=["live"],
            approved=False,
        )

        all_templates = self.planner.list_templates()
        approved_templates = self.planner.list_templates(approved_only=True)
        live_templates = self.planner.list_templates(execution_mode="live")

        self.assertEqual(len(all_templates), 2)
        self.assertEqual(len(approved_templates), 1)
        self.assertEqual(approved_templates[0].template_id, "pick-and-scan")
        self.assertEqual(len(live_templates), 2)

    def test_render_rejects_unapproved_template(self) -> None:
        self.planner.register_template(
            template_id="unapproved",
            name="Unapproved",
            steps=_base_template_steps(),
            approved=False,
        )

        with self.assertRaises(PhysicalMissionTemplateError):
            self.planner.render_plan(
                "unapproved",
                mission_id="mission-a",
                bindings={
                    "arm_device": "arm-01",
                    "sensor_device": "sensor-01",
                    "target_bin": "bin-7",
                    "scan_zone": "A1",
                },
            )

    def test_render_plan_resolves_bindings_and_payload(self) -> None:
        self.planner.register_template(
            template_id="pick-and-scan",
            name="Pick and Scan",
            steps=_base_template_steps(),
            approved=True,
        )

        plan = self.planner.render_plan(
            "pick-and-scan",
            mission_id="mission-alpha",
            execution_mode="simulation",
            bindings={
                "arm_device": "arm-01",
                "sensor_device": "sensor-01",
                "target_bin": "bin-7",
                "scan_zone": "A1",
            },
        )

        self.assertEqual(plan.template_id, "pick-and-scan")
        self.assertEqual(plan.execution_mode, "simulation")
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].device_id, "arm-01")
        self.assertEqual(plan.steps[0].payload["target"], "bin-7")
        self.assertEqual(plan.steps[0].payload["mission"], "mission-alpha")
        self.assertEqual(plan.steps[1].payload["zone"], "A1")
        self.assertTrue(plan.requires_sandbox_approval)

    def test_render_live_plan_derives_required_controls(self) -> None:
        self.planner.register_template(
            template_id="pick-and-scan",
            name="Pick and Scan",
            steps=_base_template_steps(),
            approved=True,
        )

        plan = self.planner.render_plan(
            "pick-and-scan",
            mission_id="mission-beta",
            execution_mode="live",
            bindings={
                "arm_device": "arm-01",
                "sensor_device": "sensor-01",
                "target_bin": "bin-9",
                "scan_zone": "B2",
            },
        )

        self.assertIn("simulation_approval_required", plan.required_controls)
        self.assertIn("sandbox_approval_required", plan.required_controls)
        self.assertIn("supervisor_ack_required", plan.required_controls)

    def test_render_live_critical_plan_requires_human_approval(self) -> None:
        self.planner.register_template(
            template_id="critical-maintenance",
            name="Critical Maintenance",
            steps=[
                {
                    "step_id": "critical",
                    "device_binding": "arm_device",
                    "capability_id": "arm-critical",
                    "payload_template": {"target": "{target_bin}"},
                }
            ],
            execution_modes=["live"],
            approved=True,
        )

        plan = self.planner.render_plan(
            "critical-maintenance",
            mission_id="mission-critical",
            execution_mode="live",
            bindings={
                "arm_device": "arm-01",
                "target_bin": "maintenance-bay",
            },
        )

        self.assertEqual(plan.max_risk_tier, "critical")
        self.assertIn("human_approval_required", plan.required_controls)

    def test_render_rejects_missing_binding(self) -> None:
        self.planner.register_template(
            template_id="pick-and-scan",
            name="Pick and Scan",
            steps=_base_template_steps(),
            approved=True,
        )

        with self.assertRaises(PhysicalMissionTemplateError):
            self.planner.render_plan(
                "pick-and-scan",
                mission_id="mission-gamma",
                bindings={
                    "arm_device": "arm-01",
                    "sensor_device": "sensor-01",
                    "target_bin": "bin-7",
                },
            )

    def test_render_rejects_unsupported_device_capability(self) -> None:
        self.planner.register_template(
            template_id="bad-template",
            name="Bad Template",
            steps=[
                {
                    "step_id": "bad-step",
                    "device_binding": "sensor_device",
                    "capability_id": "arm-move",
                    "payload_template": {"target": "{target_bin}"},
                }
            ],
            approved=True,
        )

        with self.assertRaises(PhysicalMissionTemplateError):
            self.planner.render_plan(
                "bad-template",
                mission_id="mission-delta",
                bindings={
                    "sensor_device": "sensor-01",
                    "target_bin": "bin-5",
                },
            )



def _base_template_steps() -> list[dict[str, Any]]:
    return [
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
            "step_id": "scan",
            "device_binding": "sensor_device",
            "capability_id": "temperature-scan",
            "payload_template": {
                "zone": "{scan_zone}",
            },
        },
    ]



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
                "metadata": {},
            },
            {
                "capability_id": "arm-critical",
                "capability_type": "actuator",
                "command": "move_arm_critical",
                "risk_tier": "critical",
                "requires_sandbox_approval": True,
                "simulation_supported": True,
                "safety_tags": ["high-force"],
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
