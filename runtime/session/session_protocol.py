"""Session protocol contract for boot, status, and priority formatting."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jsonschema import RefResolver
from jsonschema.validators import validator_for


class SessionProtocolValidationError(ValueError):
    """Raised when session protocol config is invalid."""


class SessionProtocolContract:
    """Validates and renders session boot and status protocol messages."""

    _status_regex = re.compile(r"^\[STATUS: [A-Za-z ]+ \| [0-9]{1,3}%\] - .+")
    _priority_regex = re.compile(r"^\[PRIORITY: (LOW|MEDIUM|HIGH|CRITICAL)\] - .+")

    def __init__(self, schema_path: str | Path, config: dict[str, Any]) -> None:
        self.schema_path = Path(schema_path)
        self.config = config

        if not self.schema_path.exists():
            raise FileNotFoundError(f"Session protocol schema not found: {self.schema_path}")

        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        common_schema_path = self.schema_path.parent / "common.schema.json"
        if not common_schema_path.exists():
            raise FileNotFoundError(f"Common schema not found: {common_schema_path}")
        common_schema = json.loads(common_schema_path.read_text(encoding="utf-8"))

        validator_class = validator_for(schema)
        validator_class.check_schema(schema)

        store: dict[str, dict[str, Any]] = {
            "common.schema.json": common_schema,
            "session-protocol.schema.json": schema,
        }
        common_id = common_schema.get("$id")
        schema_id = schema.get("$id")
        if common_id:
            store[common_id] = common_schema
        if schema_id:
            store[schema_id] = schema

        resolver = RefResolver.from_schema(schema, store=store)
        self._validator = validator_class(schema, resolver=resolver)

        self.validate_config(self.config)

    def validate_config(self, config: dict[str, Any]) -> None:
        errors = sorted(self._validator.iter_errors(config), key=lambda err: list(err.absolute_path))
        if errors:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
                for error in errors
            )
            raise SessionProtocolValidationError(f"Session protocol config validation failed. {details}")

    def render_boot_message(
        self,
        connected_systems: list[str] | None,
        context_summary: str | None,
        address: str = "Boss",
    ) -> str:
        boot = self.config["boot_sequence"]
        systems = ", ".join(connected_systems or ["none"]) if connected_systems is not None else "none"
        summary = context_summary or "none"

        lines = [
            boot["online_line"],
            boot["knowledge_base_line"],
            boot["connected_systems_line"].format(connected_systems=systems),
            boot["context_line"].format(context_summary=summary),
            "",
            boot["ready_prompt_template"].format(address=address),
        ]
        return "\n".join(lines)

    def format_status_update(self, state: str, progress: int, descriptor: str) -> str:
        status = self.config["status_protocol"]
        if state not in status["allowed_states"]:
            raise ValueError(f"Unsupported status state: {state}")
        if progress < 0 or progress > 100:
            raise ValueError("Progress must be between 0 and 100")
        if not descriptor:
            raise ValueError("Descriptor is required")

        return status["template"].format(state=state, progress=progress, descriptor=descriptor)

    def format_priority(self, level: str, message: str) -> str:
        priority = self.config["priority_protocol"]
        if level not in priority["allowed_levels"]:
            raise ValueError(f"Unsupported priority level: {level}")
        if not message:
            raise ValueError("Priority message is required")

        return priority["template"].format(level=level, message=message)

    def validate_status_message(self, message: str) -> bool:
        return self._status_regex.match(message) is not None

    def validate_priority_message(self, message: str) -> bool:
        return self._priority_regex.match(message) is not None
