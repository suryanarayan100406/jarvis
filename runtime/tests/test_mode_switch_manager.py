"""Tests for P3-T9 mode switch manager."""

from __future__ import annotations

import unittest

from runtime.persona import ModeSwitchManager


class ModeSwitchManagerTests(unittest.TestCase):
    def test_default_mode_and_policy(self) -> None:
        manager = ModeSwitchManager()

        self.assertEqual(manager.current_mode, "standard")
        policy = manager.get_policy()
        self.assertEqual(policy.mode, "standard")
        self.assertEqual(policy.response_tone, "balanced")

    def test_switch_mode_updates_current_and_records_transition(self) -> None:
        manager = ModeSwitchManager()

        transition = manager.switch_mode("war room", reason="incident response", actor_id="boss")

        self.assertTrue(transition.changed)
        self.assertEqual(transition.from_mode, "standard")
        self.assertEqual(transition.to_mode, "war_room")
        self.assertEqual(manager.current_mode, "war_room")

    def test_switch_to_same_mode_is_idempotent(self) -> None:
        manager = ModeSwitchManager(default_mode="stealth")

        transition = manager.switch_mode("stealth", reason="maintain low profile", actor_id="boss")

        self.assertFalse(transition.changed)
        self.assertEqual(transition.from_mode, "stealth")
        self.assertEqual(transition.to_mode, "stealth")

    def test_unknown_mode_raises(self) -> None:
        manager = ModeSwitchManager()

        with self.assertRaises(ValueError):
            manager.switch_mode("hyperdrive", reason="test", actor_id="boss")

    def test_history_returns_most_recent_first(self) -> None:
        manager = ModeSwitchManager(max_history=5)
        manager.switch_mode("war_room", reason="incident", actor_id="boss")
        manager.switch_mode("deep_research", reason="analysis", actor_id="boss")

        history = manager.history(limit=2)

        self.assertEqual([item.to_mode for item in history], ["deep_research", "war_room"])

    def test_mission_brief_mode_is_available(self) -> None:
        manager = ModeSwitchManager()

        policy = manager.get_policy("mission brief")

        self.assertEqual(policy.mode, "mission_brief")
        self.assertEqual(policy.label, "Mission Brief")


if __name__ == "__main__":
    unittest.main()
