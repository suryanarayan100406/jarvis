"""Tests for P3-T6 answer-first response formatter."""

from __future__ import annotations

import unittest

from runtime.persona import PersonaProfileEngine, ResponseFormatter


class ResponseFormatterTests(unittest.TestCase):
    def test_formats_answer_first_with_confidence_tag(self) -> None:
        formatter = ResponseFormatter()

        response = formatter.format_response(
            "All systems are stable",
            addressed_to="Boss",
            confidence=0.91,
        )

        self.assertEqual(response.confidence_label, "high")
        self.assertTrue(response.text.startswith("Boss, All systems are stable"))
        self.assertIn("[confidence:high]", response.text)
        self.assertIn("answer-first", response.tags)

    def test_formats_with_profile_defaults(self) -> None:
        engine = PersonaProfileEngine()
        profile = engine.select_profile("jarvis")
        formatter = ResponseFormatter()

        response = formatter.format_with_profile(profile, "Mission data synchronized", confidence=0.75)

        self.assertEqual(response.persona_id, "jarvis")
        self.assertEqual(response.addressed_to, "Sir or Maam")
        self.assertIn("[confidence:medium]", response.text)
        self.assertIn("persona:jarvis", response.tags)

    def test_details_are_appended_when_enabled(self) -> None:
        formatter = ResponseFormatter()

        response = formatter.format_response(
            "Threat level elevated",
            confidence=0.55,
            include_details=True,
            details="Unusual traffic detected in subsystem C.",
        )

        self.assertEqual(response.confidence_label, "low")
        self.assertIn("Details: Unusual traffic detected in subsystem C.", response.text)

    def test_unknown_confidence_when_value_missing(self) -> None:
        formatter = ResponseFormatter()

        response = formatter.format_response("Diagnostics complete", confidence=None)

        self.assertEqual(response.confidence_label, "unknown")
        self.assertIn("[confidence:unknown]", response.text)

    def test_rejects_empty_answer(self) -> None:
        formatter = ResponseFormatter()

        with self.assertRaises(ValueError):
            formatter.format_response("   \n  ")

    def test_threshold_validation(self) -> None:
        with self.assertRaises(ValueError):
            ResponseFormatter(high_threshold=1.2)

        with self.assertRaises(ValueError):
            ResponseFormatter(high_threshold=0.8, medium_threshold=0.9)


if __name__ == "__main__":
    unittest.main()
