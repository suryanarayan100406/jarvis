"""Regression tests for persona and tone consistency across conversation turns."""

from __future__ import annotations

import unittest

from runtime.persona import (
    AddressingPreferenceLayer,
    ModeSwitchManager,
    PersonaProfileEngine,
    ResponseFormatter,
)
from runtime.voice import ConversationTurnManager


class PersonaConversationRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile_engine = PersonaProfileEngine()
        self.addressing = AddressingPreferenceLayer()
        self.formatter = ResponseFormatter()
        self.turns = ConversationTurnManager()
        self.modes = ModeSwitchManager()

    def test_friday_conversation_keeps_boss_addressing(self) -> None:
        profile, formatted, completed_turn = self._run_turn(
            mode="friday",
            operator_id="boss",
            operator_role="primary_user",
            question="What is system status?",
            answer="All systems are operating within safe parameters",
            confidence=0.92,
        )

        self.assertEqual(profile.profile_id, "friday")
        self.assertTrue(formatted.text.startswith("Boss,"))
        self.assertIn("persona:friday", formatted.tags)
        self.assertEqual(completed_turn.response_text, formatted.text)

    def test_jarvis_honorific_remains_consistent_across_turns(self) -> None:
        first = self._run_turn(
            mode="jarvis",
            operator_id="boss",
            operator_role="primary_user",
            question="Give me an update",
            answer="Mission telemetry is synchronized",
            confidence=0.8,
            jarvis_honorific="Sir",
        )
        second = self._run_turn(
            mode="jarvis",
            operator_id="boss",
            operator_role="primary_user",
            question="Any threats?",
            answer="No active threats detected",
            confidence=0.77,
            jarvis_honorific="Sir",
        )

        self.assertTrue(first[1].text.startswith("Sir,"))
        self.assertTrue(second[1].text.startswith("Sir,"))
        self.assertEqual(first[0].tone, second[0].tone)

    def test_interruption_and_resume_preserve_persona_tag(self) -> None:
        profile = self.profile_engine.select_profile("friday")
        address = self.addressing.resolve_for_profile(
            profile=profile,
            operator_id="boss",
            operator_role="primary_user",
        )

        turn = self.turns.start_turn("Begin diagnostics")
        self.turns.begin_response(turn.turn_id)
        self.turns.append_response(turn.turn_id, "Initializing diagnostics")
        interrupted = self.turns.interrupt(turn.turn_id, reason="user_interrupt", utterance="hold")
        self.assertEqual(interrupted.status, "interrupted")

        self.turns.resume(turn.turn_id)
        formatted = self.formatter.format_with_profile(
            profile,
            "Diagnostics resumed",
            addressed_to=address.address,
            confidence=0.7,
        )
        self.turns.append_response(turn.turn_id, formatted.text)
        completed = self.turns.complete(turn.turn_id)

        self.assertIn("persona:friday", formatted.tags)
        self.assertIn("Diagnostics resumed", completed.response_text)

    def test_mode_switch_does_not_break_answer_first_contract(self) -> None:
        transition = self.modes.switch_mode("deep research", reason="need analysis", actor_id="boss")
        policy = self.modes.get_policy()

        profile, formatted, _ = self._run_turn(
            mode="friday",
            operator_id="boss",
            operator_role="primary_user",
            question="Summarize findings",
            answer="Initial anomaly hypothesis is being validated",
            confidence=0.73,
        )

        self.assertTrue(transition.changed)
        self.assertEqual(policy.mode, "deep_research")
        self.assertEqual(profile.profile_id, "friday")
        self.assertIn("answer-first", formatted.tags)
        self.assertIn("[confidence:medium]", formatted.text)

    def _run_turn(
        self,
        *,
        mode: str,
        operator_id: str,
        operator_role: str,
        question: str,
        answer: str,
        confidence: float,
        jarvis_honorific: str | None = None,
    ):
        profile = self.profile_engine.select_profile(mode)
        address = self.addressing.resolve_for_profile(
            profile=profile,
            operator_id=operator_id,
            operator_role=operator_role,
            jarvis_honorific=jarvis_honorific,
        )

        turn = self.turns.start_turn(question)
        self.turns.begin_response(turn.turn_id)

        formatted = self.formatter.format_with_profile(
            profile,
            answer,
            addressed_to=address.address,
            confidence=confidence,
        )

        self.turns.append_response(turn.turn_id, formatted.text)
        completed = self.turns.complete(turn.turn_id)
        return profile, formatted, completed


if __name__ == "__main__":
    unittest.main()
