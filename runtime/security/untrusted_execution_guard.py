"""Guardrails for untrusted-content execution requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable
from uuid import uuid4

NowProvider = Callable[[], datetime]


@dataclass(frozen=True)
class UntrustedExecutionAuthorization:
    token: str
    source_context: str
    approved_by: str
    content_fingerprint: str
    allowed_tools: tuple[str, ...]
    allowed_operations: tuple[str, ...]
    max_uses: int
    used_count: int
    created_at: str
    expires_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class UntrustedExecutionRequest:
    source_context: str
    content: str
    tool_name: str
    operation: str
    explicit_authorization: bool
    authorization_token: str | None = None
    command: str | None = None


@dataclass(frozen=True)
class UntrustedExecutionDecision:
    allowed: bool
    reason: str
    guardrail: str
    source_context: str
    content_fingerprint: str
    authorization_token: str | None


@dataclass
class _AuthorizationRecord:
    token: str
    source_context: str
    approved_by: str
    content_fingerprint: str
    allowed_tools: tuple[str, ...]
    allowed_operations: tuple[str, ...]
    max_uses: int
    used_count: int
    created_at: datetime
    expires_at: datetime
    metadata: dict[str, Any]
    revoked: bool = False


class UntrustedExecutionGuardError(ValueError):
    """Raised when guardrail configuration or authorization requests are invalid."""


class UntrustedContentExecutionGuard:
    """Validates execution requests originating from untrusted content sources."""

    _untrusted_sources = {"document", "web", "email", "attachment", "external"}
    _blocked_command_tokens = ("&&", "||", ";", "`", "$(", "${")

    def __init__(
        self,
        *,
        default_ttl_seconds: int = 300,
        default_max_uses: int = 1,
        now_provider: NowProvider | None = None,
    ) -> None:
        if default_ttl_seconds < 1:
            raise UntrustedExecutionGuardError("default_ttl_seconds must be at least 1")
        if default_max_uses < 1:
            raise UntrustedExecutionGuardError("default_max_uses must be at least 1")

        self.default_ttl_seconds = default_ttl_seconds
        self.default_max_uses = default_max_uses
        self._now_provider = now_provider or _utc_now
        self._authorizations: dict[str, _AuthorizationRecord] = {}

    def issue_authorization(
        self,
        *,
        source_context: str,
        content: str,
        approved_by: str,
        allowed_tools: list[str] | tuple[str, ...] | None = None,
        allowed_operations: list[str] | tuple[str, ...] | None = None,
        ttl_seconds: int | None = None,
        max_uses: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UntrustedExecutionAuthorization:
        normalized_source = _normalize_required(source_context, "source_context").lower()
        if normalized_source not in self._untrusted_sources:
            raise UntrustedExecutionGuardError("source_context must be one of the untrusted source types")

        normalized_content = _normalize_required(content, "content")
        normalized_approver = _normalize_required(approved_by, "approved_by")
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        uses = max_uses if max_uses is not None else self.default_max_uses
        if ttl < 1:
            raise UntrustedExecutionGuardError("ttl_seconds must be at least 1")
        if uses < 1:
            raise UntrustedExecutionGuardError("max_uses must be at least 1")

        now = self._now_provider()
        token = f"uex-{uuid4().hex[:24]}"
        record = _AuthorizationRecord(
            token=token,
            source_context=normalized_source,
            approved_by=normalized_approver,
            content_fingerprint=_fingerprint(normalized_content),
            allowed_tools=_normalize_set(allowed_tools),
            allowed_operations=_normalize_set(allowed_operations),
            max_uses=uses,
            used_count=0,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
            metadata=dict(metadata or {}),
        )
        self._authorizations[token] = record
        return self._to_authorization(record)

    def revoke_authorization(self, token: str) -> None:
        normalized_token = _normalize_required(token, "token")
        record = self._authorizations.get(normalized_token)
        if record is None:
            raise KeyError(f"Unknown authorization token: {normalized_token}")
        record.revoked = True

    def evaluate(self, request: UntrustedExecutionRequest) -> UntrustedExecutionDecision:
        source_context = _normalize_required(request.source_context, "source_context").lower()
        content = _normalize_required(request.content, "content")
        tool_name = _normalize_required(request.tool_name, "tool_name").lower()
        operation = _normalize_required(request.operation, "operation").lower()
        fingerprint = _fingerprint(content)

        if source_context not in self._untrusted_sources:
            return UntrustedExecutionDecision(
                allowed=True,
                reason="Source context is trusted; untrusted guardrails not required.",
                guardrail="trusted_source_bypass",
                source_context=source_context,
                content_fingerprint=fingerprint,
                authorization_token=request.authorization_token,
            )

        if not request.explicit_authorization:
            return self._deny(
                source_context=source_context,
                fingerprint=fingerprint,
                token=request.authorization_token,
                guardrail="explicit_authorization_required",
                reason="Untrusted content execution requires explicit authorization.",
            )

        if request.authorization_token is None:
            return self._deny(
                source_context=source_context,
                fingerprint=fingerprint,
                token=None,
                guardrail="authorization_token_missing",
                reason="Untrusted content execution requires a matching authorization token.",
            )

        token = _normalize_required(request.authorization_token, "authorization_token")
        record = self._authorizations.get(token)
        if record is None:
            return self._deny(
                source_context=source_context,
                fingerprint=fingerprint,
                token=token,
                guardrail="authorization_token_unknown",
                reason="Authorization token is unknown.",
            )

        now = self._now_provider()
        if record.revoked:
            return self._deny(
                source_context=source_context,
                fingerprint=fingerprint,
                token=token,
                guardrail="authorization_revoked",
                reason="Authorization token has been revoked.",
            )
        if now > record.expires_at:
            return self._deny(
                source_context=source_context,
                fingerprint=fingerprint,
                token=token,
                guardrail="authorization_expired",
                reason="Authorization token has expired.",
            )
        if record.used_count >= record.max_uses:
            return self._deny(
                source_context=source_context,
                fingerprint=fingerprint,
                token=token,
                guardrail="authorization_replay_blocked",
                reason="Authorization token usage budget has been exhausted.",
            )
        if source_context != record.source_context:
            return self._deny(
                source_context=source_context,
                fingerprint=fingerprint,
                token=token,
                guardrail="source_context_mismatch",
                reason="Authorization token is bound to a different source context.",
            )
        if fingerprint != record.content_fingerprint:
            return self._deny(
                source_context=source_context,
                fingerprint=fingerprint,
                token=token,
                guardrail="content_fingerprint_mismatch",
                reason="Authorization token does not match this untrusted content.",
            )

        if record.allowed_tools and tool_name not in record.allowed_tools:
            return self._deny(
                source_context=source_context,
                fingerprint=fingerprint,
                token=token,
                guardrail="tool_scope_denied",
                reason=f"Tool {tool_name} is outside authorized scope.",
            )
        if record.allowed_operations and operation not in record.allowed_operations:
            return self._deny(
                source_context=source_context,
                fingerprint=fingerprint,
                token=token,
                guardrail="operation_scope_denied",
                reason=f"Operation {operation} is outside authorized scope.",
            )

        if request.command is not None:
            command = _normalize_required(request.command, "command")
            if any(char in command for char in ("\n", "\r", "\x00")):
                return self._deny(
                    source_context=source_context,
                    fingerprint=fingerprint,
                    token=token,
                    guardrail="command_control_character_blocked",
                    reason="Command contains disallowed control characters.",
                )
            command_lower = command.lower()
            for blocked in self._blocked_command_tokens:
                if blocked in command_lower:
                    return self._deny(
                        source_context=source_context,
                        fingerprint=fingerprint,
                        token=token,
                        guardrail="command_blocked_token",
                        reason=f"Command contains blocked token: {blocked}",
                    )

        record.used_count += 1
        return UntrustedExecutionDecision(
            allowed=True,
            reason="Untrusted content execution request authorized under scoped guardrails.",
            guardrail="authorized_untrusted_execution",
            source_context=source_context,
            content_fingerprint=fingerprint,
            authorization_token=token,
        )

    def get_authorization(self, token: str) -> UntrustedExecutionAuthorization:
        normalized_token = _normalize_required(token, "token")
        record = self._authorizations.get(normalized_token)
        if record is None:
            raise KeyError(f"Unknown authorization token: {normalized_token}")
        return self._to_authorization(record)

    def _to_authorization(self, record: _AuthorizationRecord) -> UntrustedExecutionAuthorization:
        return UntrustedExecutionAuthorization(
            token=record.token,
            source_context=record.source_context,
            approved_by=record.approved_by,
            content_fingerprint=record.content_fingerprint,
            allowed_tools=record.allowed_tools,
            allowed_operations=record.allowed_operations,
            max_uses=record.max_uses,
            used_count=record.used_count,
            created_at=_to_iso(record.created_at),
            expires_at=_to_iso(record.expires_at),
            metadata=dict(record.metadata),
        )

    @staticmethod
    def _deny(
        *,
        source_context: str,
        fingerprint: str,
        token: str | None,
        guardrail: str,
        reason: str,
    ) -> UntrustedExecutionDecision:
        return UntrustedExecutionDecision(
            allowed=False,
            reason=reason,
            guardrail=guardrail,
            source_context=source_context,
            content_fingerprint=fingerprint,
            authorization_token=token,
        )


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise UntrustedExecutionGuardError(f"{field_name} is required")
    return normalized


def _normalize_set(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    normalized = sorted({_normalize_required(value, "scope_value").lower() for value in values})
    return tuple(normalized)


def _fingerprint(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "UntrustedContentExecutionGuard",
    "UntrustedExecutionAuthorization",
    "UntrustedExecutionDecision",
    "UntrustedExecutionGuardError",
    "UntrustedExecutionRequest",
]
