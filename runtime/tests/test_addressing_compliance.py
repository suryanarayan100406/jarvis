"""Tests for P12-T5 addressing compliance for Boss and user overrides."""

from __future__ import annotations

import unittest

from runtime.persona import (
    AddressingPreferenceLayer,
    PersonaProfileEngine,
    ResponseFormatter,
)


class AddressingComplianceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PersonaProfileEngine()
        self.layer = AddressingPreferenceLayer()
        self.formatter = ResponseFormatter()

    def test_friday_primary_user_defaults_to_boss(self) -> None:
        profile = self.engine.select_profile("friday")

        resolution = self.layer.resolve_for_profile(
            profile=profile,
            operator_id="boss",
            operator_role="primary_user",
        )

        self.assertEqual(resolution.address, "Boss")
        self.assertEqual(resolution.source, "role_override")

        formatted = self.formatter.format_with_profile(
            profile,
            "All systems nominal",
            addressed_to=resolution.address,
            confidence=0.91,
        )
        self.assertTrue(formatted.text.startswith("Boss,"))

    def test_friday_operator_override_replaces_boss(self) -> None:
        profile = self.engine.select_profile("friday")

        resolution = self.layer.resolve_for_profile(
            profile=profile,
            operator_id="boss",
            operator_role="primary_user",
            operator_overrides={"boss": "Captain"},
        )

        self.assertEqual(resolution.address, "Captain")
        self.assertEqual(resolution.source, "operator_override")

        formatted = self.formatter.format_with_profile(
            profile,
            "Deployment complete",
            addressed_to=resolution.address,
            confidence=0.84,
        )
        self.assertTrue(formatted.text.startswith("Captain,"))

    def test_operator_override_is_scoped_to_matching_user(self) -> None:
        profile = self.engine.select_profile("friday")
        overrides = {"boss": "Chief"}

        boss_resolution = self.layer.resolve_for_profile(
            profile=profile,
            operator_id="boss",
            operator_role="primary_user",
            operator_overrides=overrides,
        )
        guest_resolution = self.layer.resolve_for_profile(
            profile=profile,
            operator_id="guest",
            operator_role="primary_user",
            operator_overrides=overrides,
        )

        self.assertEqual(boss_resolution.address, "Chief")
        self.assertEqual(boss_resolution.source, "operator_override")
        self.assertEqual(guest_resolution.address, "Boss")
        self.assertEqual(guest_resolution.source, "role_override")

    def test_operator_override_value_is_whitespace_normalized(self) -> None:
        profile = self.engine.select_profile("friday")

        resolution = self.layer.resolve_for_profile(
            profile=profile,
            operator_id="boss",
            operator_role="primary_user",
            operator_overrides={"boss": "  Mission   Control  "},
        )

        self.assertEqual(resolution.address, "Mission Control")
        self.assertEqual(resolution.source, "operator_override")

    def test_jarvis_user_override_takes_precedence_over_honorific(self) -> None:
        profile = self.engine.select_profile("jarvis")

        resolution = self.layer.resolve_for_profile(
            profile=profile,
            operator_id="boss",
            operator_role="primary_user",
            operator_overrides={"boss": "Director"},
            jarvis_honorific="Sir",
        )

        self.assertEqual(resolution.address, "Director")
        self.assertEqual(resolution.source, "operator_override")


if __name__ == "__main__":
    unittest.main()
