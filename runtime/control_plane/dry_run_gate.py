"""Dry-run execution gate for potentially destructive control-plane operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from .connector_manager import ConnectorExecutionResult, ConnectorManager

ExecutionMode = Literal["dry_run", "execute"]


@dataclass(frozen=True)
class DryRunClassification:
    is_destructive: bool
    reason: str


@dataclass(frozen=True)
class DryRunPreview:
    token: str
    host_id: str
    hostname: str
    operation: str
    adapter_name: str | None
    identity: str | None
    payload: dict[str, Any]
    command: str | None
    is_destructive: bool
    reason: str
    generated_at: str


@dataclass(frozen=True)
class DryRunExecutionOutcome:
    mode: ExecutionMode
    preview: DryRunPreview | None
    execution: ConnectorExecutionResult | None


class DryRunGateError(ValueError):
    """Raised when dry-run gating rules are violated."""


class DryRunExecutionGate:
    """Enforces preview-first execution for destructive operations."""

    destructive_operation_keywords = {
        "restart",
        "stop",
        "terminate",
        "shutdown",
        "reboot",
        "delete",
        "drop",
        "disable",
        "kill",
        "wipe",
        "format",
        "rollback",
    }

    destructive_command_keywords = {
        "rm -rf",
        "mkfs",
        "dd if=",
        "shutdown",
        "reboot",
        "systemctl stop",
        "systemctl restart",
        "drop database",
        "truncate table",
        "kill -9",
    }

    def __init__(self, connector_manager: ConnectorManager) -> None:
        self.connector_manager = connector_manager
        self._previews: dict[str, DryRunPreview] = {}

    def execute(
        self,
        host_id: str,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        adapter_name: str | None = None,
        identity: str | None = None,
        dry_run: bool = False,
        dry_run_token: str | None = None,
    ) -> DryRunExecutionOutcome:
        if dry_run:
            preview = self.preview_operation(
                host_id,
                operation,
                payload,
                adapter_name=adapter_name,
                identity=identity,
            )
            return DryRunExecutionOutcome(mode="dry_run", preview=preview, execution=None)

        preview = self._validate_destructive_execution(
            host_id,
            operation,
            payload,
            adapter_name=adapter_name,
            identity=identity,
            dry_run_token=dry_run_token,
        )
        execution = self.connector_manager.execute(
            host_id,
            operation,
            payload,
            adapter_name=adapter_name,
            identity=identity,
        )
        return DryRunExecutionOutcome(mode="execute", preview=preview, execution=execution)

    def preview_operation(
        self,
        host_id: str,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        adapter_name: str | None = None,
        identity: str | None = None,
    ) -> DryRunPreview:
        host = self.connector_manager.inventory.get_host(host_id)
        normalized_operation = _normalize_required(operation, "operation").lower()
        request_payload = dict(payload or {})
        command = _extract_command(request_payload)
        classification = self.classify(normalized_operation, command)
        normalized_adapter_name = _normalize_optional(adapter_name)
        normalized_identity = _normalize_optional(identity)

        token = self._build_preview_token(
            host_id=host.host_id,
            operation=normalized_operation,
            payload=request_payload,
            adapter_name=normalized_adapter_name,
            identity=normalized_identity,
        )
        preview = DryRunPreview(
            token=token,
            host_id=host.host_id,
            hostname=host.hostname,
            operation=normalized_operation,
            adapter_name=normalized_adapter_name,
            identity=normalized_identity,
            payload=dict(request_payload),
            command=command,
            is_destructive=classification.is_destructive,
            reason=classification.reason,
            generated_at=_utc_now_iso(),
        )
        self._previews[token] = preview
        return preview

    def classify(self, operation: str, command: str | None = None) -> DryRunClassification:
        normalized_operation = _normalize_required(operation, "operation").lower()
        for keyword in self.destructive_operation_keywords:
            if keyword in normalized_operation:
                return DryRunClassification(
                    is_destructive=True,
                    reason=f"Operation includes destructive keyword: {keyword}",
                )

        if command is not None:
            command_lower = command.lower()
            for keyword in self.destructive_command_keywords:
                if keyword in command_lower:
                    return DryRunClassification(
                        is_destructive=True,
                        reason=f"Command includes destructive keyword: {keyword}",
                    )

        return DryRunClassification(is_destructive=False, reason="Operation classified as non-destructive")

    def _validate_destructive_execution(
        self,
        host_id: str,
        operation: str,
        payload: dict[str, Any] | None,
        *,
        adapter_name: str | None,
        identity: str | None,
        dry_run_token: str | None,
    ) -> DryRunPreview | None:
        normalized_operation = _normalize_required(operation, "operation").lower()
        request_payload = dict(payload or {})
        command = _extract_command(request_payload)
        classification = self.classify(normalized_operation, command)
        if not classification.is_destructive:
            return None

        normalized_adapter_name = _normalize_optional(adapter_name)
        normalized_identity = _normalize_optional(identity)
        if dry_run_token is None:
            raise DryRunGateError(
                "Destructive operation requires dry_run token. Generate one using dry_run=True first."
            )

        normalized_token = _normalize_required(dry_run_token, "dry_run_token")
        preview = self._previews.get(normalized_token)
        if preview is None:
            raise DryRunGateError("Unknown or expired dry_run token")

        expected_token = self._build_preview_token(
            host_id=host_id,
            operation=normalized_operation,
            payload=request_payload,
            adapter_name=normalized_adapter_name,
            identity=normalized_identity,
        )
        if expected_token != normalized_token:
            raise DryRunGateError("dry_run token does not match the current execution payload")

        self._previews.pop(normalized_token, None)
        return preview

    def _build_preview_token(
        self,
        *,
        host_id: str,
        operation: str,
        payload: dict[str, Any],
        adapter_name: str | None,
        identity: str | None,
    ) -> str:
        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        canonical = json.dumps(
            {
                "host_id": _normalize_required(host_id, "host_id"),
                "operation": _normalize_required(operation, "operation").lower(),
                "adapter_name": adapter_name or "",
                "identity": identity or "",
                "payload": canonical_payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = sha256(canonical.encode("utf-8")).hexdigest()
        return f"dryrun-{digest[:24]}"


def _extract_command(payload: dict[str, Any]) -> str | None:
    if "command" not in payload:
        return None

    command = payload["command"]
    if not isinstance(command, str):
        raise DryRunGateError("payload.command must be a string when provided")
    if any(char in command for char in ("\n", "\r", "\x00")):
        raise DryRunGateError("payload.command contains disallowed control characters")

    return _normalize_required(command, "payload.command")


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise DryRunGateError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
