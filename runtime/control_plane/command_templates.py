"""Role-scoped command template library for control-plane operations."""

from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from typing import Any

from .host_inventory import HostInventoryService, HostRecord


@dataclass(frozen=True)
class CommandTemplateRecord:
    template_id: str
    operation: str
    command_template: str
    host_roles: tuple[str, ...]
    required_parameters: tuple[str, ...]
    description: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ResolvedCommand:
    template_id: str
    operation: str
    host_role: str
    command: str
    parameters: dict[str, str]


class CommandTemplateError(ValueError):
    """Raised when template registration or resolution violates constraints."""


class CommandTemplateLibrary:
    """Registers, resolves, and matches command templates by host role and operation."""

    def __init__(self, allowed_roles: set[str] | None = None) -> None:
        self.allowed_roles = set(allowed_roles or HostInventoryService.allowed_roles)
        self._templates: dict[str, CommandTemplateRecord] = {}

    def register_template(
        self,
        *,
        template_id: str,
        operation: str,
        command_template: str,
        host_roles: list[str] | tuple[str, ...],
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CommandTemplateRecord:
        normalized_template_id = _normalize_required(template_id, "template_id").lower()
        if normalized_template_id in self._templates:
            raise CommandTemplateError(f"Template already exists: {normalized_template_id}")

        if not isinstance(command_template, str):
            raise CommandTemplateError("command_template must be a string")
        _ensure_safe_command(command_template, field_name="command_template")

        normalized_operation = _normalize_required(operation, "operation").lower()
        normalized_command_template = _normalize_required(command_template, "command_template")

        normalized_roles = self._normalize_roles(host_roles)
        required_parameters = _extract_required_parameters(normalized_command_template)

        record = CommandTemplateRecord(
            template_id=normalized_template_id,
            operation=normalized_operation,
            command_template=normalized_command_template,
            host_roles=normalized_roles,
            required_parameters=required_parameters,
            description=_normalize_optional(description),
            metadata=dict(metadata or {}),
        )
        self._templates[normalized_template_id] = record
        return record

    def get_template(self, template_id: str) -> CommandTemplateRecord:
        normalized_template_id = _normalize_required(template_id, "template_id").lower()
        record = self._templates.get(normalized_template_id)
        if record is None:
            raise KeyError(f"Unknown template: {normalized_template_id}")
        return record

    def list_templates(
        self,
        *,
        host_role: str | None = None,
        operation: str | None = None,
    ) -> list[CommandTemplateRecord]:
        normalized_role = self._normalize_role(host_role) if host_role is not None else None
        normalized_operation = _normalize_required(operation, "operation").lower() if operation is not None else None

        records = list(self._templates.values())
        if normalized_role is not None:
            records = [record for record in records if normalized_role in record.host_roles]
        if normalized_operation is not None:
            records = [record for record in records if record.operation == normalized_operation]

        records.sort(key=lambda record: record.template_id)
        return records

    def is_operation_allowed(self, *, host_role: str, operation: str) -> bool:
        normalized_role = self._normalize_role(host_role)
        normalized_operation = _normalize_required(operation, "operation").lower()
        return any(
            record.operation == normalized_operation and normalized_role in record.host_roles
            for record in self._templates.values()
        )

    def allowed_operations(self, *, host_role: str) -> tuple[str, ...]:
        normalized_role = self._normalize_role(host_role)
        operations = sorted(
            {
                record.operation
                for record in self._templates.values()
                if normalized_role in record.host_roles
            }
        )
        return tuple(operations)

    def resolve_template(
        self,
        template_id: str,
        *,
        host_role: str,
        parameters: dict[str, Any] | None = None,
    ) -> ResolvedCommand:
        record = self.get_template(template_id)
        normalized_role = self._normalize_role(host_role)
        if normalized_role not in record.host_roles:
            allowed = ", ".join(record.host_roles)
            raise CommandTemplateError(
                f"Host role {normalized_role} is not allowed for template {record.template_id}. "
                f"Allowed roles: {allowed}"
            )

        normalized_parameters = self._normalize_parameters(parameters or {})
        missing = [parameter for parameter in record.required_parameters if parameter not in normalized_parameters]
        if missing:
            raise CommandTemplateError(
                f"Template {record.template_id} is missing required parameters: {', '.join(missing)}"
            )

        try:
            rendered_command = record.command_template.format(**normalized_parameters)
        except Exception as exc:
            raise CommandTemplateError(
                f"Failed to render template {record.template_id}: {type(exc).__name__}: {exc}"
            ) from exc

        normalized_command = _normalize_required(rendered_command, "rendered_command")
        _ensure_safe_command(normalized_command, field_name="rendered_command")

        return ResolvedCommand(
            template_id=record.template_id,
            operation=record.operation,
            host_role=normalized_role,
            command=normalized_command,
            parameters=normalized_parameters,
        )

    def resolve_for_host(
        self,
        template_id: str,
        *,
        host: HostRecord,
        parameters: dict[str, Any] | None = None,
    ) -> ResolvedCommand:
        return self.resolve_template(template_id, host_role=host.role, parameters=parameters)

    def _normalize_parameters(self, parameters: dict[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in parameters.items():
            normalized_key = _normalize_required(str(key), "parameter_key")
            raw_value = str(value)
            _ensure_safe_command(raw_value, field_name=f"parameter:{normalized_key}")
            normalized_value = _normalize_required(raw_value, f"parameter:{normalized_key}")
            normalized[normalized_key] = normalized_value
        return normalized

    def _normalize_roles(self, host_roles: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        if not host_roles:
            raise CommandTemplateError("host_roles must include at least one role")

        normalized = sorted({_normalize_required(role, "host_role").lower() for role in host_roles})
        unsupported = [role for role in normalized if role not in self.allowed_roles]
        if unsupported:
            allowed = ", ".join(sorted(self.allowed_roles))
            raise CommandTemplateError(
                f"Unsupported host roles: {', '.join(unsupported)}. Allowed: {allowed}"
            )
        return tuple(normalized)

    def _normalize_role(self, role: str) -> str:
        normalized = _normalize_required(role, "host_role").lower()
        if normalized not in self.allowed_roles:
            allowed = ", ".join(sorted(self.allowed_roles))
            raise CommandTemplateError(f"Unsupported host role: {role}. Allowed: {allowed}")
        return normalized


def _extract_required_parameters(template: str) -> tuple[str, ...]:
    required: set[str] = set()
    formatter = Formatter()

    for _, field_name, _, _ in formatter.parse(template):
        if field_name is None:
            continue
        if field_name.isdigit():
            raise CommandTemplateError("Positional fields are not supported in command templates")

        root_field = field_name.split(".", 1)[0].split("[", 1)[0]
        normalized = _normalize_required(root_field, "template_parameter")
        required.add(normalized)

    return tuple(sorted(required))


def _ensure_safe_command(value: str, *, field_name: str) -> None:
    if any(char in value for char in ("\n", "\r", "\x00")):
        raise CommandTemplateError(f"{field_name} contains disallowed control characters")


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise CommandTemplateError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None
