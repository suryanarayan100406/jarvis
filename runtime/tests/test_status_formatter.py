"""Tests for P3-T7 status update formatter."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from runtime.session import SessionProtocolContract, StatusUpdateFormatter

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "schemas" / "v1" / "session-protocol.schema.json"
EXAMPLE_PATH = ROOT / "contracts" / "schemas" / "v1" / "examples" / "session-protocol.example.json"


class StatusUpdateFormatterTests(unittest.TestCase):
    def setUp(self) -> None:
        config = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        contract = SessionProtocolContract(SCHEMA_PATH, config)
        self.formatter = StatusUpdateFormatter(contract=contract)

    def test_format_uses_contract_shape(self) -> None:
        result = self.formatter.format_update("In Progress", 45, "Synchronizing telemetry")

        self.assertEqual(result.message, "[STATUS: In Progress | 45%] - Synchronizing telemetry")
        self.assertTrue(self.formatter.validate(result.message))

    def test_format_supports_address_and_suffix_metadata(self) -> None:
        result = self.formatter.format_update(
            "Blocked",
            70,
            "Awaiting approval",
            address="Boss",
            task_id="P3-T7",
            eta_seconds=12,
        )

        self.assertTrue(result.message.startswith("Boss, [STATUS: Blocked | 70%] - Awaiting approval"))
        self.assertIn("(TASK: P3-T7; ETA: 12s)", result.message)

    def test_fallback_formatter_works_without_contract(self) -> None:
        formatter = StatusUpdateFormatter()

        result = formatter.format_update("In Progress", 10, "Bootstrapping")

        self.assertEqual(result.message, "[STATUS: In Progress | 10%] - Bootstrapping")
        self.assertTrue(formatter.validate(result.message))

    def test_invalid_progress_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.formatter.format_update("In Progress", 120, "Overflow")

    def test_invalid_eta_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.formatter.format_update("In Progress", 50, "Working", eta_seconds=-1)

    def test_invalid_state_raises_when_contract_present(self) -> None:
        with self.assertRaises(ValueError):
            self.formatter.format_update("Unknown", 10, "Bad state")


if __name__ == "__main__":
    unittest.main()
