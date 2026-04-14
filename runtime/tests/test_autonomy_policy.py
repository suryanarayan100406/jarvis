"""Tests for P6-T5 bounded autonomy policy."""

from __future__ import annotations

import unittest

from runtime.orchestration import AutonomyPolicyError, BoundedAutonomyPolicy


class BoundedAutonomyPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = BoundedAutonomyPolicy()

    def test_low_risk_auto_approve_is_allowed(self) -> None:
        decision = self.policy.evaluate(
            action_id="act-1",
            risk_level="low",
            route="auto_approve",
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.mode, "autonomous")
        self.assertEqual(decision.required_controls, ())

    def test_medium_destructive_adds_dry_run_control(self) -> None:
        decision = self.policy.evaluate(
            action_id="act-2",
            risk_level="medium",
            route="auto_approve",
            is_destructive=True,
        )

        self.assertTrue(decision.allowed)
        self.assertIn("dry_run_required", decision.required_controls)
        self.assertIn("policy_trace_required", decision.required_controls)

    def test_high_risk_auto_approve_is_blocked(self) -> None:
        decision = self.policy.evaluate(
            action_id="act-3",
            risk_level="high",
            route="auto_approve",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.mode, "blocked")

    def test_high_risk_supervisor_route_is_allowed(self) -> None:
        decision = self.policy.evaluate(
            action_id="act-4",
            risk_level="high",
            route="requires_supervisor",
            is_destructive=True,
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.mode, "supervised")
        self.assertIn("supervisor_ack_required", decision.required_controls)
        self.assertIn("dry_run_required", decision.required_controls)

    def test_critical_risk_requires_manual_escalation(self) -> None:
        decision = self.policy.evaluate(
            action_id="act-5",
            risk_level="critical",
            route="escalate_human",
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.mode, "manual")
        self.assertIn("human_approval_required", decision.required_controls)

    def test_invalid_inputs_raise(self) -> None:
        with self.assertRaises(AutonomyPolicyError):
            self.policy.evaluate(action_id="act-x", risk_level="severe", route="auto_approve")

        with self.assertRaises(AutonomyPolicyError):
            self.policy.evaluate(action_id="act-y", risk_level="low", route="manual")


if __name__ == "__main__":
    unittest.main()
