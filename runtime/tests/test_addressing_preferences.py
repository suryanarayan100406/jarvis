"""Tests for P3-T5 addressing preference layer."""

from __future__ import annotations

import unittest

from runtime.persona import AddressingPreferenceLayer, PersonaProfile


class AddressingPreferenceLayerTests(unittest.TestCase):
    def test_role_override_used_for_friday_primary_user(self) -> None:
        layer = AddressingPreferenceLayer()

        resolution = layer.resolve(operator_id="boss", operator_role="primary_user", mode="friday")

        self.assertEqual(resolution.address, "Boss")
        self.assertEqual(resolution.source, "role_override")

    def test_operator_override_takes_precedence(self) -> None:
        layer = AddressingPreferenceLayer()

        resolution = layer.resolve(
            operator_id="op-7",
            operator_role="limited_user",
            mode="friday",
            operator_overrides={"op-7": "Captain"},
        )

        self.assertEqual(resolution.address, "Captain")
        self.assertEqual(resolution.source, "operator_override")

    def test_jarvis_honorific_override_is_supported(self) -> None:
        layer = AddressingPreferenceLayer()

        resolution = layer.resolve(
            operator_id="boss",
            operator_role="primary_user",
            mode="jarvis",
            jarvis_honorific="Maam",
        )

        self.assertEqual(resolution.address, "Maam")
        self.assertEqual(resolution.source, "jarvis_honorific")

    def test_mode_default_used_when_role_not_mapped(self) -> None:
        layer = AddressingPreferenceLayer(role_overrides={"friday": {}, "jarvis": {}})

        resolution = layer.resolve(operator_id="guest", operator_role="guest", mode="friday")

        self.assertEqual(resolution.address, "Boss")
        self.assertEqual(resolution.source, "mode_default")

    def test_resolve_for_profile_falls_back_to_profile_default(self) -> None:
        custom_profile = PersonaProfile(
            profile_id="stealth",
            display_name="STEALTH",
            addressing_default="Operator",
            tone=("quiet", "precise"),
            response_style="answer-first stealth",
            confidence_style="explicit-confidence-tag",
            safety_posture=("policy-first",),
        )
        layer = AddressingPreferenceLayer()

        resolution = layer.resolve_for_profile(
            profile=custom_profile,
            operator_id="x",
            operator_role="observer",
        )

        self.assertEqual(resolution.address, "Operator")
        self.assertEqual(resolution.source, "profile_default")

    def test_unsupported_mode_raises(self) -> None:
        layer = AddressingPreferenceLayer()

        with self.assertRaises(ValueError):
            layer.resolve(operator_id="x", operator_role="observer", mode="unknown")


if __name__ == "__main__":
    unittest.main()
