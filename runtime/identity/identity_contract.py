"""Identity directive and addressing preference contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import RefResolver
from jsonschema.validators import validator_for


class IdentityDirectiveValidationError(ValueError):
    """Raised when identity directive payload is invalid."""


class IdentityDirectiveContract:
    """Validates identity directives and resolves runtime form-of-address behavior."""

    def __init__(self, schema_path: str | Path) -> None:
        self.schema_path = Path(schema_path)
        if not self.schema_path.exists():
            raise FileNotFoundError(f"Identity schema not found: {self.schema_path}")

        self._schema: dict[str, Any] = json.loads(self.schema_path.read_text(encoding="utf-8"))
        common_schema_path = self.schema_path.parent / "common.schema.json"
        if not common_schema_path.exists():
            raise FileNotFoundError(f"Common schema not found: {common_schema_path}")
        common_schema: dict[str, Any] = json.loads(common_schema_path.read_text(encoding="utf-8"))

        validator_class = validator_for(self._schema)
        validator_class.check_schema(self._schema)

        store: dict[str, dict[str, Any]] = {
            "common.schema.json": common_schema,
            "identity-directive.schema.json": self._schema,
        }
        common_schema_id = common_schema.get("$id")
        identity_schema_id = self._schema.get("$id")
        if common_schema_id:
            store[common_schema_id] = common_schema
        if identity_schema_id:
            store[identity_schema_id] = self._schema

        resolver = RefResolver.from_schema(self._schema, store=store)
        self._validator = validator_class(self._schema, resolver=resolver)

    def validate_directive(self, directive: dict[str, Any]) -> None:
        if not isinstance(directive, dict):
            raise TypeError("Identity directive must be a dictionary")

        errors = sorted(self._validator.iter_errors(directive), key=lambda err: list(err.absolute_path))
        if errors:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
                for error in errors
            )
            raise IdentityDirectiveValidationError(f"Identity directive validation failed. {details}")

    def resolve_address(
        self,
        directive: dict[str, Any],
        operator_id: str,
        operator_role: str,
        mode: str = "friday",
        jarvis_honorific: str | None = None,
    ) -> str:
        """Resolve address string according to identity directive contract."""
        self.validate_directive(directive)

        if directive["allow_address_override"] and operator_role in directive["authorized_override_roles"]:
            override = directive["address_overrides"].get(operator_id)
            if override:
                return override

        if mode == "jarvis" and directive["jarvis_mode"]["enabled"]:
            if jarvis_honorific in {"Sir", "Maam"}:
                return jarvis_honorific
            return directive["jarvis_mode"]["default_honorific"]

        return directive["default_address"]
