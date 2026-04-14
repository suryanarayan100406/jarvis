"""Tests for P1-T6 deterministic run state machine."""

from __future__ import annotations

import unittest

from runtime.orchestration import InvalidRunTransitionError, RunStateMachine


class RunStateMachineTests(unittest.TestCase):
    def test_success_path_transitions_in_order(self) -> None:
        machine = RunStateMachine(run_id="run-001")

        machine.transition_to("execute", reason="planned")
        machine.transition_to("validate", reason="executed")
        machine.transition_to("report", reason="validated")
        machine.transition_to("completed", reason="reported")

        self.assertEqual(machine.current_stage, "completed")
        self.assertEqual(len(machine.history), 4)
        self.assertEqual(machine.history[0].from_stage, "plan")
        self.assertEqual(machine.history[-1].to_stage, "completed")

    def test_invalid_transition_is_blocked(self) -> None:
        machine = RunStateMachine(run_id="run-002")

        with self.assertRaises(InvalidRunTransitionError):
            machine.transition_to("report", reason="skip_steps")

    def test_terminal_states_reject_additional_transitions(self) -> None:
        machine = RunStateMachine(run_id="run-003")
        machine.transition_to("execute")
        machine.transition_to("failed", reason="error")

        with self.assertRaises(InvalidRunTransitionError):
            machine.transition_to("validate")

    def test_advance_success_reaches_completed(self) -> None:
        machine = RunStateMachine(run_id="run-004")

        machine.advance_success()
        machine.advance_success()
        machine.advance_success()
        machine.advance_success()

        self.assertEqual(machine.current_stage, "completed")

    def test_advance_success_fails_in_terminal_state(self) -> None:
        machine = RunStateMachine(run_id="run-005")
        machine.transition_to("cancelled", reason="operator_stop")

        with self.assertRaises(InvalidRunTransitionError):
            machine.advance_success()

    def test_transition_records_include_reason_and_timestamp(self) -> None:
        machine = RunStateMachine(run_id="run-006")
        record = machine.transition_to("execute", reason="plan_ready")

        self.assertEqual(record.reason, "plan_ready")
        self.assertTrue(record.timestamp.endswith("Z"))


if __name__ == "__main__":
    unittest.main()
