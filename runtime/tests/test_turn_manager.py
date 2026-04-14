"""Tests for P3-T3 conversational turn manager."""

from __future__ import annotations

import unittest

from runtime.voice import ConversationTurnManager, TurnStateError


class ConversationTurnManagerTests(unittest.TestCase):
    def test_turn_lifecycle_with_response_accumulation(self) -> None:
        manager = ConversationTurnManager()

        turn = manager.start_turn("Give me a status update")
        turn = manager.begin_response(turn.turn_id)
        turn = manager.append_response(turn.turn_id, "All systems")
        turn = manager.append_response(turn.turn_id, "operating nominally")
        turn = manager.complete(turn.turn_id)

        self.assertEqual(turn.status, "completed")
        self.assertEqual(turn.response_text, "All systems operating nominally")
        self.assertIsNone(manager.get_active_turn())

    def test_new_turn_interrupts_active_turn(self) -> None:
        manager = ConversationTurnManager()

        first = manager.start_turn("Open diagnostics")
        manager.begin_response(first.turn_id)

        second = manager.start_turn("Cancel that")
        updated_first = manager.get_turn(first.turn_id)

        self.assertEqual(updated_first.status, "interrupted")
        self.assertEqual(len(updated_first.interruptions), 1)
        self.assertEqual(updated_first.interruptions[0].reason, "new_turn_started")
        self.assertEqual(second.user_input, "Cancel that")

    def test_interrupt_and_resume_flow(self) -> None:
        manager = ConversationTurnManager()

        turn = manager.start_turn("Start analysis")
        manager.begin_response(turn.turn_id)
        interrupted = manager.interrupt(turn.turn_id, reason="user_interrupt", utterance="hold on")

        self.assertEqual(interrupted.status, "interrupted")
        self.assertEqual(interrupted.interruptions[-1].utterance, "hold on")

        resumed = manager.resume(turn.turn_id)
        resumed = manager.append_response(resumed.turn_id, "Resuming analysis")

        self.assertEqual(resumed.status, "responding")
        self.assertEqual(resumed.response_text, "Resuming analysis")

    def test_append_requires_responding_state(self) -> None:
        manager = ConversationTurnManager()
        turn = manager.start_turn("Status")

        with self.assertRaises(TurnStateError):
            manager.append_response(turn.turn_id, "Should fail")

    def test_list_recent_returns_most_recent_first(self) -> None:
        manager = ConversationTurnManager()

        first = manager.start_turn("First")
        second = manager.start_turn("Second")
        third = manager.start_turn("Third")

        recent = manager.list_recent(limit=2)

        self.assertEqual([turn.turn_id for turn in recent], [third.turn_id, second.turn_id])
        self.assertEqual(manager.get_turn(first.turn_id).status, "interrupted")

    def test_limit_must_be_positive(self) -> None:
        manager = ConversationTurnManager()

        with self.assertRaises(ValueError):
            manager.list_recent(limit=0)


if __name__ == "__main__":
    unittest.main()
