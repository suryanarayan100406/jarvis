"""Strict schema validation middleware for FRIDAY tool contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import RefResolver
from jsonschema.validators import validator_for

from .exceptions import SchemaValidationMiddlewareError, ValidationFailure

SCHEMA_FILES: dict[str, str] = {
    "common": "common.schema.json",
    "tool_request": "tool-request.schema.json",
    "tool_response": "tool-response.schema.json",
    "tool_error": "tool-error.schema.json",
    "telemetry_envelope": "telemetry-envelope.schema.json",
}


class SchemaValidationMiddleware:
    """Validates payloads against versioned FRIDAY schema contracts."""

    def __init__(self, schema_root: str | Path) -> None:
        self.schema_root = Path(schema_root)
        self._schemas: dict[str, dict[str, Any]] = {}
        self._validators: dict[str, Any] = {}
        self._initialize()

    def _initialize(self) -> None:
        if not self.schema_root.exists():
            raise FileNotFoundError(f"Schema root does not exist: {self.schema_root}")

        for key, file_name in SCHEMA_FILES.items():
            schema_path = self.schema_root / file_name
            if not schema_path.exists():
                raise FileNotFoundError(f"Missing schema file: {schema_path}")
            self._schemas[key] = json.loads(schema_path.read_text(encoding="utf-8"))

        store: dict[str, dict[str, Any]] = {}
        for key, schema in self._schemas.items():
            schema_id = schema.get("$id")
            if schema_id:
                store[schema_id] = schema
            store[SCHEMA_FILES[key]] = schema

        for contract_type in ("tool_request", "tool_response", "tool_error", "telemetry_envelope"):
            schema = self._schemas[contract_type]
            validator_class = validator_for(schema)
            validator_class.check_schema(schema)
            resolver = RefResolver.from_schema(schema, store=store)
            self._validators[contract_type] = validator_class(schema, resolver=resolver)

    def validate(self, contract_type: str, payload: dict[str, Any]) -> None:
        """Validate payload and raise SchemaValidationMiddlewareError on failure."""
        if contract_type not in self._validators:
            raise ValueError(f"Unsupported contract type: {contract_type}")
        if not isinstance(payload, dict):
            raise TypeError("Payload must be a dictionary object")

        validator = self._validators[contract_type]
        errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))

        if errors:
            failures = [
                ValidationFailure(
                    path=".".join(str(part) for part in error.absolute_path) or "<root>",
                    message=error.message,
                )
                for error in errors
            ]
            raise SchemaValidationMiddlewareError(contract_type=contract_type, failures=failures)

    def validate_request(self, payload: dict[str, Any]) -> None:
        self.validate("tool_request", payload)

    def validate_response(self, payload: dict[str, Any]) -> None:
        self.validate("tool_response", payload)

    def validate_error(self, payload: dict[str, Any]) -> None:
        self.validate("tool_error", payload)

    def validate_telemetry(self, payload: dict[str, Any]) -> None:
        self.validate("telemetry_envelope", payload)


def enforce_schema(contract_type: str, payload: dict[str, Any], middleware: SchemaValidationMiddleware) -> dict[str, Any]:
    """Fail-fast middleware entry point.

    Returns payload unchanged on success and raises on any schema failure.
    """
    middleware.validate(contract_type, payload)
    return payload
