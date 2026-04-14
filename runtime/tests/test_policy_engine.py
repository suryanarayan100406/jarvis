"""Tests for P1-T3 policy engine behavior."""

from __future__ import annotations

import unittest

from runtime.policy import PolicyEngine, PolicyRequest


class PolicyEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PolicyEngine()

    def test_low_risk_allows_primary_user(self) -> None:
        decision = self.engine.evaluate(
            PolicyRequest(
                actor_role="primary_user",
                tool_name="terminal",
                tool_action="get_status",
                target_scope="local",
                environment="dev",
            )
        )

        self.assertEqual(decision.risk_tier, "low")
        self.assertEqual(decision.decision, "allow")
        self.assertTrue(decision.rule_id)
        self.assertTrue(decision.reason)
        self.assertTrue(decision.evaluated_at.endswith("Z"))

    def test_medium_risk_requires_approval_for_limited_user(self) -> None:
        decision = self.engine.evaluate(
            PolicyRequest(
                actor_role="limited_user",
                tool_name="filesystem",
                tool_action="write_config",
                target_scope="local",
                environment="dev",
            )
        )

        self.assertEqual(decision.risk_tier, "medium")
        self.assertEqual(decision.decision, "require_approval")
        self.assertEqual(decision.rule_id, "policy.limited.medium.require_approval")

    def test_high_risk_requires_approval(self) -> None:
        decision = self.engine.evaluate(
            PolicyRequest(
                actor_role="authorized_operator",
                tool_name="service",
                tool_action="stop_service",
                target_scope="service",
                environment="dev",
            )
        )

        self.assertEqual(decision.risk_tier, "high")
        self.assertEqual(decision.decision, "require_approval")
        self.assertEqual(decision.rule_id, "policy.high.require_approval")

    def test_critical_risk_requires_approval_for_primary_user(self) -> None:
        decision = self.engine.evaluate(
            PolicyRequest(
                actor_role="primary_user",
                tool_name="door_lock",
                tool_action="unlock_main_door",
                target_scope="physical",
                environment="prod",
            )
        )

        self.assertEqual(decision.risk_tier, "critical")
        self.assertEqual(decision.decision, "require_approval")
        self.assertEqual(decision.rule_id, "policy.critical.require_approval")

    def test_limited_user_critical_is_denied(self) -> None:
        decision = self.engine.evaluate(
            PolicyRequest(
                actor_role="limited_user",
                tool_name="iot",
                tool_action="unlock_all",
                target_scope="physical",
                environment="prod",
            )
        )

        self.assertEqual(decision.risk_tier, "critical")
        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "policy.limited.high_critical.deny")

    def test_production_escalates_medium_to_high(self) -> None:
        decision = self.engine.evaluate(
            PolicyRequest(
                actor_role="authorized_operator",
                tool_name="filesystem",
                tool_action="update_config",
                target_scope="local",
                environment="prod",
            )
        )

        self.assertEqual(decision.risk_tier, "high")
        self.assertEqual(decision.decision, "require_approval")

    def test_dry_run_demotes_high_to_medium(self) -> None:
        decision = self.engine.evaluate(
            PolicyRequest(
                actor_role="authorized_operator",
                tool_name="service",
                tool_action="restart_service",
                target_scope="service",
                environment="dev",
                dry_run=True,
            )
        )

        self.assertEqual(decision.risk_tier, "medium")
        self.assertEqual(decision.decision, "allow")

    def test_declared_risk_tier_can_only_raise_risk(self) -> None:
        payload = {
            "actor": {"role": "authorized_operator"},
            "tool": {"name": "terminal", "action": "get_status"},
            "target": {"scope": "local", "environment": "dev"},
            "execution": {"dry_run": False},
            "policy_context": {"risk_tier": "critical"},
        }

        decision = self.engine.evaluate(payload)

        self.assertEqual(decision.risk_tier, "critical")
        self.assertEqual(decision.decision, "require_approval")

    def test_unknown_role_is_denied(self) -> None:
        decision = self.engine.evaluate(
            PolicyRequest(
                actor_role="guest",
                tool_name="terminal",
                tool_action="get_status",
                target_scope="local",
                environment="dev",
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "policy.actor.unknown.deny")


if __name__ == "__main__":
    unittest.main()
