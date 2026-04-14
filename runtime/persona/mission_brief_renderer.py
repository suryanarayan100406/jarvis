"""Mission Brief schema validation and rendering utilities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from jsonschema.validators import validator_for


class MissionBriefValidationError(ValueError):
    """Raised when a mission brief payload does not satisfy schema requirements."""


class MissionBriefRenderer:
    """Builds, validates, and renders mission brief payloads."""

    def __init__(self) -> None:
        self.schema = _mission_brief_schema()
        validator_class = validator_for(self.schema)
        validator_class.check_schema(self.schema)
        self._validator = validator_class(self.schema)

    def build_brief(
        self,
        *,
        title: str,
        objective: str,
        context: str,
        tasks: Iterable[str | dict[str, Any]],
        owner: str = "Boss",
        status: str = "In Progress",
        priority: str = "HIGH",
        constraints: Iterable[str] | None = None,
        risks: Iterable[str] | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_tasks = self._normalize_tasks(tasks)
        brief = {
            "mission_id": mission_id or str(uuid4()),
            "title": _normalize_text(title),
            "objective": _normalize_text(objective),
            "context": _normalize_text(context),
            "status": _normalize_text(status),
            "priority": _normalize_text(priority).upper(),
            "owner": _normalize_text(owner),
            "tasks": normalized_tasks,
            "constraints": [_normalize_text(item) for item in (constraints or []) if _normalize_text(item)],
            "risks": [_normalize_text(item) for item in (risks or []) if _normalize_text(item)],
            "generated_at": _utc_now_iso(),
        }

        self.validate_brief(brief)
        return brief

    def validate_brief(self, brief: dict[str, Any]) -> None:
        errors = sorted(self._validator.iter_errors(brief), key=lambda err: list(err.absolute_path))
        if errors:
            detail = "; ".join(
                f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
                for error in errors
            )
            raise MissionBriefValidationError(f"Mission brief validation failed. {detail}")

    def render_markdown(self, brief: dict[str, Any]) -> str:
        self.validate_brief(brief)

        lines = [
            f"# Mission Brief: {brief['title']}",
            f"- Mission ID: {brief['mission_id']}",
            f"- Status: {brief['status']}",
            f"- Priority: {brief['priority']}",
            f"- Owner: {brief['owner']}",
            "",
            "## Objective",
            brief["objective"],
            "",
            "## Context",
            brief["context"],
            "",
            "## Execution Steps",
        ]

        for task in brief["tasks"]:
            lines.append(f"1. [{task['status']}] {task['description']} (owner: {task['owner']})")

        lines.extend(["", "## Constraints"])
        if brief["constraints"]:
            for item in brief["constraints"]:
                lines.append(f"- {item}")
        else:
            lines.append("- none")

        lines.extend(["", "## Risks"])
        if brief["risks"]:
            for item in brief["risks"]:
                lines.append(f"- {item}")
        else:
            lines.append("- none")

        return "\n".join(lines)

    def render_json(self, brief: dict[str, Any]) -> str:
        self.validate_brief(brief)
        return json.dumps(brief, sort_keys=True, separators=(",", ":"))

    def render(self, brief: dict[str, Any], *, output_format: str = "markdown") -> str:
        normalized_format = output_format.lower().strip()
        if normalized_format == "markdown":
            return self.render_markdown(brief)
        if normalized_format == "json":
            return self.render_json(brief)
        raise ValueError(f"Unsupported output format: {output_format}")

    def _normalize_tasks(self, tasks: Iterable[str | dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, task in enumerate(tasks, start=1):
            if isinstance(task, str):
                description = _normalize_text(task)
                owner = "unassigned"
                status = "pending"
            elif isinstance(task, dict):
                description = _normalize_text(str(task.get("description", "")))
                owner = _normalize_text(str(task.get("owner", "unassigned")))
                status = _normalize_text(str(task.get("status", "pending"))).lower()
            else:
                raise TypeError("Each task must be a string or dictionary")

            if not description:
                raise ValueError(f"Task description is required for step {index}")

            normalized.append(
                {
                    "step_id": f"S{index:03d}",
                    "description": description,
                    "owner": owner or "unassigned",
                    "status": status or "pending",
                }
            )

        return normalized


def _mission_brief_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "mission_id",
            "title",
            "objective",
            "context",
            "status",
            "priority",
            "owner",
            "tasks",
            "constraints",
            "risks",
            "generated_at",
        ],
        "properties": {
            "mission_id": {"type": "string", "minLength": 1},
            "title": {"type": "string", "minLength": 1},
            "objective": {"type": "string", "minLength": 1},
            "context": {"type": "string", "minLength": 1},
            "status": {"type": "string", "enum": ["Planned", "In Progress", "Blocked", "Completed"]},
            "priority": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
            "owner": {"type": "string", "minLength": 1},
            "tasks": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["step_id", "description", "owner", "status"],
                    "properties": {
                        "step_id": {"type": "string", "minLength": 1},
                        "description": {"type": "string", "minLength": 1},
                        "owner": {"type": "string", "minLength": 1},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "done", "blocked"]},
                    },
                },
            },
            "constraints": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "risks": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "generated_at": {"type": "string", "minLength": 1},
        },
    }


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
