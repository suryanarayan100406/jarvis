"""Tests for P12-T8 prompt handling no-filler and answer-first contracts."""

from __future__ import annotations

import unittest

from runtime.persona import PersonaProfileEngine, ResponseFormatter


class PromptHandlingContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.formatter = ResponseFormatter()
        self.engine = PersonaProfileEngine()

    def test_strips_leading_filler_and_keeps_answer_first(self) -> None:
        response = self.formatter.format_response(
            "Sure, incident response is active",
            addressed_to="Boss",
            confidence=0.9,
        )

        self.assertEqual(response.answer, "incident response is active")
        self.assertTrue(response.text.startswith("Boss, incident response is active"))
        self.assertIn("answer-first", response.tags)

    def test_strips_multiple_leading_filler_prefixes(self) -> None:
        response = self.formatter.format_response(
            "Absolutely, certainly, no problem: diagnostics complete",
            addressed_to="Boss",
            confidence=0.7,
        )

        self.assertEqual(response.answer, "diagnostics complete")
        self.assertTrue(response.text.startswith("Boss, diagnostics complete"))

    def test_non_filler_response_is_preserved(self) -> None:
        response = self.formatter.format_response(
            "Mission telemetry is synchronized",
            addressed_to="Boss",
            confidence=0.82,
        )

        self.assertEqual(response.answer, "Mission telemetry is synchronized")
        self.assertTrue(response.text.startswith("Boss, Mission telemetry is synchronized"))

    def test_profile_formatting_keeps_no_filler_contract(self) -> None:
        profile = self.engine.select_profile("jarvis")

        response = self.formatter.format_with_profile(
            profile,
            "Of course, mission data synchronized",
            confidence=0.76,
        )

        self.assertEqual(response.persona_id, "jarvis")
        self.assertEqual(response.answer, "mission data synchronized")
        self.assertTrue(response.text.startswith("Sir or Maam, mission data synchronized"))
        self.assertIn("persona:jarvis", response.tags)

    def test_rejects_answer_when_only_filler_is_provided(self) -> None:
        with self.assertRaises(ValueError):
            self.formatter.format_response("Sure, absolutely, no problem")


if __name__ == "__main__":
    unittest.main()
