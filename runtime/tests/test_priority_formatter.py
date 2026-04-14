"""Tests for P3-T8 priority formatter."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from runtime.session import PriorityAlertFormatter, SessionProtocolContract

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "schemas" / "v1" / "session-protocol.schema.json"
EXAMPLE_PATH = ROOT / "contracts" / "schemas" / "v1" / "examples" / "session-protocol.example.json"


class PriorityAlertFormatterTests(unittest.TestCase):
    def setUp(self) -> None:
        config = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        contract = SessionProtocolContract(SCHEMA_PATH, config)
        self.formatter = PriorityAlertFormatter(contract=contract)

    def test_format_critical_alert_uses_contract_shape(self) -> None:
        result = self.formatter.format_alert("CRITICAL", "Kill-switch activated")

        self.assertEqual(result.message, "[PRIORITY: CRITICAL] - Kill-switch activated")
        self.assertTrue(self.formatter.validate(result.message))

    def test_format_supports_address_and_escalation_metadata(self) -> None:
        result = self.formatter.format_alert(
            "HIGH",
            "Thermal threshold exceeded",
            address="Boss",
            escalation_hint="notify safety officer",
            requires_ack=True,
        )

        self.assertTrue(result.message.startswith("Boss, [PRIORITY: HIGH] - Thermal threshold exceeded"))
        self.assertIn("ESCALATE: notify safety officer", result.message)
        self.assertIn("ACK_REQUIRED", result.message)

    def test_fallback_formatter_rejects_unknown_level(self) -> None:
        formatter = PriorityAlertFormatter()

        with self.assertRaises(ValueError):
            formatter.format_alert("URGENT", "not allowed")

    def test_contract_formatter_rejects_unknown_level(self) -> None:
        with self.assertRaises(ValueError):
            self.formatter.format_alert("URGENT", "not allowed")

    def test_is_urgent_classification(self) -> None:
        self.assertTrue(self.formatter.is_urgent("HIGH"))
        self.assertTrue(self.formatter.is_urgent("critical"))
        self.assertFalse(self.formatter.is_urgent("medium"))

    def test_details_required(self) -> None:
        with self.assertRaises(ValueError):
            self.formatter.format_alert("LOW", "   ")


if __name__ == "__main__":
    unittest.main()
