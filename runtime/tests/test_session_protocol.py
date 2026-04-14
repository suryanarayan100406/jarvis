"""Tests for P1-T11 session protocol contract and format behavior."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from runtime.session import SessionProtocolContract, SessionProtocolValidationError

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "schemas" / "v1" / "session-protocol.schema.json"
EXAMPLE_PATH = ROOT / "contracts" / "schemas" / "v1" / "examples" / "session-protocol.example.json"


class SessionProtocolContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        self.contract = SessionProtocolContract(SCHEMA_PATH, self.config)

    def test_valid_config_passes(self) -> None:
        self.contract.validate_config(self.config)

    def test_boot_message_format(self) -> None:
        message = self.contract.render_boot_message(
            connected_systems=["laptop", "server-1"],
            context_summary="P1-T10 completed",
            address="Boss",
        )

        self.assertIn("FRIDAY online. Running system check...", message)
        self.assertIn("- Connected systems: laptop, server-1", message)
        self.assertIn("Ready, Boss. What are we working on?", message)

    def test_status_format_and_validation(self) -> None:
        status = self.contract.format_status_update("In Progress", 60, "Running policy verification")

        self.assertEqual(status, "[STATUS: In Progress | 60%] - Running policy verification")
        self.assertTrue(self.contract.validate_status_message(status))

    def test_priority_format_and_validation(self) -> None:
        priority = self.contract.format_priority("CRITICAL", "Kill-switch activated")

        self.assertEqual(priority, "[PRIORITY: CRITICAL] - Kill-switch activated")
        self.assertTrue(self.contract.validate_priority_message(priority))

    def test_invalid_progress_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.contract.format_status_update("In Progress", 150, "Overflow")

    def test_invalid_status_state_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.contract.format_status_update("Unknown", 10, "Bad state")

    def test_invalid_priority_level_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.contract.format_priority("URGENT", "Not allowed")

    def test_invalid_config_raises_validation_error(self) -> None:
        invalid = dict(self.config)
        invalid.pop("status_protocol")

        with self.assertRaises(SessionProtocolValidationError):
            SessionProtocolContract(SCHEMA_PATH, invalid)


if __name__ == "__main__":
    unittest.main()
