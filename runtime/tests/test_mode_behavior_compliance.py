"""Tests for P12-T6 mode-specific behavior compliance contracts."""

from __future__ import annotations

import unittest

from runtime.persona import ModePolicy, ModeSwitchManager


class ModeBehaviorComplianceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = ModeSwitchManager()

    def test_war_room_mode_policy_contract(self) -> None:
        transition = self.manager.switch_mode("war room", reason="incident triage", actor_id="boss")
        policy = self.manager.get_policy()

        self.assertTrue(transition.changed)
        self.assertEqual(transition.to_mode, "war_room")
        self._assert_policy(
            policy,
            mode="war_room",
            label="War Room",
            response_tone="decisive",
            max_parallel_tasks=6,
            requires_confirmation=True,
        )

    def test_deep_research_mode_policy_contract(self) -> None:
        transition = self.manager.switch_mode("deep research", reason="investigate anomaly", actor_id="boss")
        policy = self.manager.get_policy()

        self.assertTrue(transition.changed)
        self.assertEqual(transition.to_mode, "deep_research")
        self._assert_policy(
            policy,
            mode="deep_research",
            label="Deep Research",
            response_tone="analytical",
            max_parallel_tasks=4,
            requires_confirmation=False,
        )

    def test_stealth_mode_policy_contract(self) -> None:
        transition = self.manager.switch_mode("stealth", reason="low-noise execution", actor_id="boss")
        policy = self.manager.get_policy()

        self.assertTrue(transition.changed)
        self.assertEqual(transition.to_mode, "stealth")
        self._assert_policy(
            policy,
            mode="stealth",
            label="Stealth",
            response_tone="minimal",
            max_parallel_tasks=1,
            requires_confirmation=True,
        )

    def test_creative_mode_policy_contract(self) -> None:
        transition = self.manager.switch_mode("creative", reason="ideation session", actor_id="boss")
        policy = self.manager.get_policy()

        self.assertTrue(transition.changed)
        self.assertEqual(transition.to_mode, "creative")
        self._assert_policy(
            policy,
            mode="creative",
            label="Creative",
            response_tone="exploratory",
            max_parallel_tasks=3,
            requires_confirmation=False,
        )

    def test_mission_brief_mode_policy_contract(self) -> None:
        transition = self.manager.switch_mode("mission brief", reason="structured handoff", actor_id="boss")
        policy = self.manager.get_policy()

        self.assertTrue(transition.changed)
        self.assertEqual(transition.to_mode, "mission_brief")
        self._assert_policy(
            policy,
            mode="mission_brief",
            label="Mission Brief",
            response_tone="structured",
            max_parallel_tasks=2,
            requires_confirmation=True,
        )

    def test_required_modes_are_transitionable_and_in_history(self) -> None:
        sequence = [
            "war room",
            "deep research",
            "stealth",
            "creative",
            "mission brief",
        ]

        for raw_mode in sequence:
            transition = self.manager.switch_mode(raw_mode, reason=f"compliance test for {raw_mode}", actor_id="boss")
            self.assertTrue(transition.changed)

        self.assertEqual(self.manager.current_mode, "mission_brief")
        recent = self.manager.history(limit=5)
        self.assertEqual(
            [item.to_mode for item in recent],
            ["mission_brief", "creative", "stealth", "deep_research", "war_room"],
        )

    def _assert_policy(
        self,
        policy: ModePolicy,
        *,
        mode: str,
        label: str,
        response_tone: str,
        max_parallel_tasks: int,
        requires_confirmation: bool,
    ) -> None:
        self.assertEqual(policy.mode, mode)
        self.assertEqual(policy.label, label)
        self.assertEqual(policy.response_tone, response_tone)
        self.assertEqual(policy.max_parallel_tasks, max_parallel_tasks)
        self.assertEqual(policy.requires_confirmation, requires_confirmation)


if __name__ == "__main__":
    unittest.main()
