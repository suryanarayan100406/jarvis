"""Tests for P3-T4 persona profile engine."""

from __future__ import annotations

import unittest

from runtime.persona import PersonaProfileEngine


class PersonaProfileEngineTests(unittest.TestCase):
    def test_select_friday_profile_defaults(self) -> None:
        engine = PersonaProfileEngine()

        profile = engine.select_profile("friday")

        self.assertEqual(profile.profile_id, "friday")
        self.assertEqual(profile.addressing_default, "Boss")
        self.assertIn("decisive", profile.tone)

    def test_select_jarvis_profile_defaults(self) -> None:
        engine = PersonaProfileEngine()

        profile = engine.select_profile("JARVIS")

        self.assertEqual(profile.profile_id, "jarvis")
        self.assertEqual(profile.addressing_default, "Sir or Maam")
        self.assertIn("polite", profile.tone)

    def test_invalid_mode_raises(self) -> None:
        engine = PersonaProfileEngine()

        with self.assertRaises(ValueError):
            engine.select_profile("sentinel")

    def test_allowed_overrides_apply(self) -> None:
        engine = PersonaProfileEngine()

        profile = engine.select_profile(
            "friday",
            overrides={
                "addressing_default": "Commander",
                "response_style": "answer-first tactical",
                "tone": ["calm", "focused"],
            },
        )

        self.assertEqual(profile.addressing_default, "Commander")
        self.assertEqual(profile.response_style, "answer-first tactical")
        self.assertEqual(profile.tone, ("calm", "focused"))

    def test_unknown_override_fields_are_ignored(self) -> None:
        engine = PersonaProfileEngine()

        profile = engine.select_profile("jarvis", overrides={"unknown": "value"})

        self.assertEqual(profile.profile_id, "jarvis")
        self.assertEqual(profile.addressing_default, "Sir or Maam")

    def test_anchor_contains_core_contract_fields(self) -> None:
        engine = PersonaProfileEngine()
        profile = engine.select_profile("friday")

        anchor = engine.build_anchor(profile)

        self.assertEqual(anchor["persona_id"], "friday")
        self.assertEqual(anchor["display_name"], "FRIDAY")
        self.assertTrue(anchor["answer_first"])
        self.assertIn("policy-first", anchor["safety_posture"])


if __name__ == "__main__":
    unittest.main()
