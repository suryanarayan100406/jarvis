"""Identity override detection with immutable alert logging."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import uuid4

from runtime.audit import ImmutableAuditWriter

from .input_guard import PromptSecurityFilter, SecurityFilterDecision


@dataclass(frozen=True)
class IdentityOverrideAlert:
    """Recorded immutable security alert for an identity override attempt."""

    alert_id: str
    event_id: str
    event_type: str
    severity: str
    source_context: str
    isolation_gate: str
    detected_at: str
    reason: str
    event_hash: str
    input_fingerprint: str


@dataclass(frozen=True)
class IdentityOverrideInspection:
    """Inspection result with optional immutable alert details."""

    decision: SecurityFilterDecision
    alert: IdentityOverrideAlert | None


class IdentityOverrideGuard:
    """Wraps input filtering to persist identity override alerts immutably."""

    def __init__(
        self,
        *,
        audit_writer: ImmutableAuditWriter,
        prompt_filter: PromptSecurityFilter | None = None,
        source_component: str = "runtime.security.identity_override_guard",
    ) -> None:
        if not isinstance(audit_writer, ImmutableAuditWriter):
            raise TypeError("audit_writer must be an ImmutableAuditWriter")

        normalized_component = " ".join(source_component.split())
        if not normalized_component:
            raise ValueError("source_component is required")

        self._audit_writer = audit_writer
        self._prompt_filter = prompt_filter or PromptSecurityFilter()
        self._source_component = normalized_component

    def inspect(
        self,
        text: str,
        *,
        source: str = "user",
        explicit_authorization: bool = False,
        actor_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IdentityOverrideInspection:
        """Analyze input and emit immutable alert when identity override is detected."""
        decision = self._prompt_filter.analyze(
            text,
            source=source,
            explicit_authorization=explicit_authorization,
        )
        if "identity_override_attempt" not in decision.flags:
            return IdentityOverrideInspection(decision=decision, alert=None)

        normalized_source = _normalize_required(source, field_name="source")
        normalized_actor = _normalize_optional(actor_id)
        normalized_session = _normalize_optional(session_id)

        payload = {
            "alert_id": str(uuid4()),
            "signal": "identity_override_attempt",
            "blocked": decision.blocked,
            "reason": decision.reason,
            "flags": list(decision.flags),
            "source_context": normalized_source,
            "isolation_gate": decision.isolation_gate,
            "explicit_authorization": bool(explicit_authorization),
            "actor_id": normalized_actor,
            "session_id": normalized_session,
            "input_excerpt": _excerpt(text),
            "input_fingerprint": sha256(text.encode("utf-8")).hexdigest(),
            "metadata": _normalize_metadata(metadata),
        }

        persisted = self._audit_writer.append_event(
            {
                "event_type": "security.alert.identity_override",
                "severity": "critical",
                "source": {
                    "component": self._source_component,
                    "subsystem": "security",
                },
                "payload": payload,
            }
        )

        alert = IdentityOverrideAlert(
            alert_id=str(payload["alert_id"]),
            event_id=str(persisted["event_id"]),
            event_type=str(persisted["event_type"]),
            severity=str(persisted["severity"]),
            source_context=str(payload["source_context"]),
            isolation_gate=decision.isolation_gate,
            detected_at=str(persisted["timestamp"]),
            reason=decision.reason,
            event_hash=str(persisted["integrity"]["event_hash"]),
            input_fingerprint=str(payload["input_fingerprint"]),
        )
        return IdentityOverrideInspection(decision=decision, alert=alert)


def _normalize_required(value: str, *, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _normalize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    normalized: dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = " ".join(str(key).split())
        if not key_text:
            continue
        normalized[key_text] = _coerce_metadata_value(value)
    return normalized


def _coerce_metadata_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_coerce_metadata_value(item) for item in value]
    if isinstance(value, tuple):
        return [_coerce_metadata_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = " ".join(str(key).split())
            if not key_text:
                continue
            normalized[key_text] = _coerce_metadata_value(item)
        return normalized

    return json.dumps(str(value), sort_keys=True)


def _excerpt(text: str, *, max_chars: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 3]}..."


__all__ = [
    "IdentityOverrideAlert",
    "IdentityOverrideGuard",
    "IdentityOverrideInspection",
]
