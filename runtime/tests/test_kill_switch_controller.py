"""Tests for P1-T5 kill-switch controller and global stop signaling."""

from __future__ import annotations

import unittest

from runtime.control import KillSwitchActivatedError, KillSwitchController


class KillSwitchControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = KillSwitchController()

    def test_activation_sets_global_stop_signal(self) -> None:
        event = self.controller.activate(reason="incident_detected", actor="system")

        self.assertTrue(self.controller.is_active())
        self.assertEqual(event.state, "active")
        self.assertEqual(event.reason, "incident_detected")

    def test_registered_hooks_are_triggered(self) -> None:
        captured: list[str] = []

        def hook(_event) -> None:
            captured.append("called")

        self.controller.register_halt_hook("executor", hook)
        event = self.controller.activate(reason="emergency_stop", actor="boss")

        self.assertEqual(captured, ["called"])
        self.assertIn("executor", event.triggered_hooks)

    def test_hook_errors_are_recorded_but_activation_persists(self) -> None:
        def bad_hook(_event) -> None:
            raise RuntimeError("hook failed")

        self.controller.register_halt_hook("bad", bad_hook)
        event = self.controller.activate(reason="emergency_stop", actor="boss")

        self.assertTrue(self.controller.is_active())
        self.assertEqual(len(event.hook_errors), 1)
        self.assertIn("bad", event.hook_errors[0])

    def test_activation_is_idempotent(self) -> None:
        first = self.controller.activate(reason="first", actor="system")
        second = self.controller.activate(reason="second", actor="system")

        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(len(self.controller.history), 1)

    def test_guard_blocks_execution_when_active(self) -> None:
        self.controller.activate(reason="critical", actor="system")

        with self.assertRaises(KillSwitchActivatedError):
            self.controller.assert_can_execute()

    def test_run_guarded_executes_when_inactive(self) -> None:
        result = self.controller.run_guarded(lambda x, y: x + y, 2, 3)
        self.assertEqual(result, 5)

    def test_wait_for_stop_observes_signal(self) -> None:
        self.controller.activate(reason="critical", actor="system")
        self.assertTrue(self.controller.wait_for_stop(timeout=0.01))

    def test_reset_clears_signal(self) -> None:
        self.controller.activate(reason="critical", actor="system")
        event = self.controller.reset(reason="resolved", actor="boss")

        self.assertFalse(self.controller.is_active())
        self.assertEqual(event.state, "inactive")


if __name__ == "__main__":
    unittest.main()
