"""Tests for P1-T9 identity directive schema and addressing contract."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from runtime.identity import IdentityDirectiveContract, IdentityDirectiveValidationError

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "schemas" / "v1" / "identity-directive.schema.json"
EXAMPLE_PATH = ROOT / "contracts" / "schemas" / "v1" / "examples" / "identity-directive.example.json"


class IdentityDirectiveContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = IdentityDirectiveContract(SCHEMA_PATH)
        self.directive = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    def test_valid_directive_passes(self) -> None:
        self.contract.validate_directive(self.directive)

    def test_default_address_is_boss(self) -> None:
        address = self.contract.resolve_address(
            directive=self.directive,
            operator_id="unknown_user",
            operator_role="primary_user",
            mode="friday",
        )
        self.assertEqual(address, "Boss")

    def test_authorized_override_is_applied(self) -> None:
        address = self.contract.resolve_address(
            directive=self.directive,
            operator_id="ops_lead",
            operator_role="authorized_operator",
            mode="friday",
        )
        self.assertEqual(address, "Chief")

    def test_unauthorized_override_is_ignored(self) -> None:
        restricted = dict(self.directive)
        restricted["authorized_override_roles"] = ["primary_user"]

        address = self.contract.resolve_address(
            directive=restricted,
            operator_id="ops_lead",
            operator_role="authorized_operator",
            mode="friday",
        )
        self.assertEqual(address, "Boss")

    def test_jarvis_mode_uses_honorific(self) -> None:
        address = self.contract.resolve_address(
            directive=self.directive,
            operator_id="unknown_user",
            operator_role="primary_user",
            mode="jarvis",
        )
        self.assertEqual(address, "Sir")

    def test_jarvis_custom_honorific_overrides_default(self) -> None:
        address = self.contract.resolve_address(
            directive=self.directive,
            operator_id="unknown_user",
            operator_role="primary_user",
            mode="jarvis",
            jarvis_honorific="Maam",
        )
        self.assertEqual(address, "Maam")

    def test_invalid_directive_fails_validation(self) -> None:
        invalid = dict(self.directive)
        invalid.pop("default_address")

        with self.assertRaises(IdentityDirectiveValidationError):
            self.contract.validate_directive(invalid)


if __name__ == "__main__":
    unittest.main()
