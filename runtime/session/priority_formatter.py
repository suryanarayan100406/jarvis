"""Priority formatter for urgent and critical operational events."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .session_protocol import SessionProtocolContract


@dataclass(frozen=True)
class FormattedPriorityAlert:
    message: str
    level: str
    details: str
    addressed_to: str | None
    escalation_hint: str | None
    requires_ack: bool


class PriorityAlertFormatter:
    """Formats priority alerts with contract enforcement and escalation metadata."""

    _priority_regex = re.compile(r"\[PRIORITY: (LOW|MEDIUM|HIGH|CRITICAL)\] - .+")
    _fallback_levels = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

    def __init__(self, contract: SessionProtocolContract | None = None) -> None:
        self.contract = contract

    def format_alert(
        self,
        level: str,
        details: str,
        *,
        address: str | None = None,
        escalation_hint: str | None = None,
        requires_ack: bool = False,
    ) -> FormattedPriorityAlert:
        normalized_level = " ".join(level.split()).upper()
        normalized_details = " ".join(details.split())
        normalized_address = " ".join(address.split()) if address else None
        normalized_escalation = " ".join(escalation_hint.split()) if escalation_hint else None

        if not normalized_details:
            raise ValueError("details is required")

        if self.contract is not None:
            base = self.contract.format_priority(normalized_level, normalized_details)
        else:
            if normalized_level not in self._fallback_levels:
                allowed = ", ".join(sorted(self._fallback_levels))
                raise ValueError(f"Unsupported priority level: {normalized_level}. Allowed: {allowed}")
            base = f"[PRIORITY: {normalized_level}] - {normalized_details}"

        if not self.validate(base):
            raise ValueError(f"Priority message does not match required shape: {base}")

        final = f"{normalized_address}, {base}" if normalized_address else base

        suffix_parts: list[str] = []
        if normalized_escalation:
            suffix_parts.append(f"ESCALATE: {normalized_escalation}")
        if requires_ack:
            suffix_parts.append("ACK_REQUIRED")
        if suffix_parts:
            final = f"{final} ({'; '.join(suffix_parts)})"

        return FormattedPriorityAlert(
            message=final,
            level=normalized_level,
            details=normalized_details,
            addressed_to=normalized_address,
            escalation_hint=normalized_escalation,
            requires_ack=requires_ack,
        )

    def is_urgent(self, level: str) -> bool:
        normalized = " ".join(level.split()).upper()
        return normalized in {"HIGH", "CRITICAL"}

    def validate(self, message: str) -> bool:
        return self._priority_regex.search(message) is not None
