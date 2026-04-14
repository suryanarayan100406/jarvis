"""Tests for P1-T2 schema validation middleware."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from runtime.validation import SchemaValidationMiddleware, SchemaValidationMiddlewareError

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "schemas" / "v1"
EXAMPLES_DIR = SCHEMA_DIR / "examples"


def _load_example(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


class SchemaValidationMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.middleware = SchemaValidationMiddleware(SCHEMA_DIR)

    def test_valid_tool_request_passes(self) -> None:
        payload = _load_example("tool-request.example.json")
        self.middleware.validate_request(payload)

    def test_valid_tool_response_passes(self) -> None:
        payload = _load_example("tool-response.example.json")
        self.middleware.validate_response(payload)

    def test_valid_tool_error_passes(self) -> None:
        payload = _load_example("tool-error.example.json")
        self.middleware.validate_error(payload)

    def test_valid_telemetry_envelope_passes(self) -> None:
        payload = _load_example("telemetry-envelope.example.json")
        self.middleware.validate_telemetry(payload)

    def test_missing_required_field_fails_strictly(self) -> None:
        payload = _load_example("tool-request.example.json")
        payload.pop("trace")

        with self.assertRaises(SchemaValidationMiddlewareError):
            self.middleware.validate_request(payload)

    def test_failed_response_without_errors_fails(self) -> None:
        payload = _load_example("tool-response.example.json")
        payload["status"] = "failed"
        payload.pop("errors", None)

        with self.assertRaises(SchemaValidationMiddlewareError):
            self.middleware.validate_response(payload)

    def test_unsupported_contract_type_fails(self) -> None:
        payload = _load_example("tool-request.example.json")

        with self.assertRaises(ValueError):
            self.middleware.validate("not_a_contract", payload)

    def test_non_object_payload_fails(self) -> None:
        with self.assertRaises(TypeError):
            self.middleware.validate_request(["invalid"])


if __name__ == "__main__":
    unittest.main()
