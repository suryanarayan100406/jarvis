"""Tests for P2-T3 planner interface adapter and deterministic serialization."""

from __future__ import annotations

import unittest

from runtime.pipeline.models import RunContext
from runtime.planner import PlannerInterfaceAdapter


class PlannerInterfaceAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = PlannerInterfaceAdapter()

    def test_same_goal_produces_same_plan_id(self) -> None:
        payload_a = self.adapter.build_plan_payload("Collect logs and summarize findings")
        payload_b = self.adapter.build_plan_payload("Collect logs and summarize findings")

        self.assertEqual(payload_a["plan_id"], payload_b["plan_id"])
        self.assertEqual(payload_a["serialized_plan"], payload_b["serialized_plan"])

    def test_different_goal_changes_plan_id(self) -> None:
        payload_a = self.adapter.build_plan_payload("Collect logs")
        payload_b = self.adapter.build_plan_payload("Collect metrics")

        self.assertNotEqual(payload_a["plan_id"], payload_b["plan_id"])

    def test_constraints_key_order_does_not_change_serialization(self) -> None:
        payload_a = self.adapter.build_plan_payload(
            "Run diagnostics",
            constraints={"timeout": 60, "env": "prod"},
        )
        payload_b = self.adapter.build_plan_payload(
            "Run diagnostics",
            constraints={"env": "prod", "timeout": 60},
        )

        self.assertEqual(payload_a["plan_id"], payload_b["plan_id"])
        self.assertEqual(payload_a["serialized_plan"], payload_b["serialized_plan"])

    def test_plan_result_contains_serialized_metadata(self) -> None:
        context = RunContext(run_id="run-serialization", goal="Check services and report", actor_id="boss")

        result = self.adapter.plan(context)

        self.assertEqual(result.metadata["serializer"], "deterministic-v1")
        self.assertIn("serialized_plan", result.metadata)
        self.assertGreater(len(result.tasks), 0)


if __name__ == "__main__":
    unittest.main()
