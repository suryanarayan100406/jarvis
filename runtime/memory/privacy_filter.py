"""Memory privacy filters and redaction helpers for retrieval safety."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RedactionResult:
    original_text: str
    redacted_text: str
    redaction_count: int
    categories: tuple[str, ...]


class MemoryPrivacyFilter:
    """Applies deterministic redaction to sensitive text and metadata fields."""

    _TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
        (
            "email",
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            "<REDACTED:EMAIL>",
        ),
        (
            "api_key",
            re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
            "<REDACTED:API_KEY>",
        ),
        (
            "token",
            re.compile(r"\b(?:token|secret|password)\s*[:=]\s*[^\s,;]+", re.IGNORECASE),
            "<REDACTED:TOKEN>",
        ),
        (
            "ip",
            re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
            "<REDACTED:IP>",
        ),
    )

    def __init__(self, sensitive_metadata_keys: set[str] | None = None) -> None:
        keys = sensitive_metadata_keys or {
            "password",
            "secret",
            "token",
            "api_key",
            "access_key",
            "private_key",
            "credential",
            "authorization",
            "email",
        }
        self._sensitive_metadata_keys = {key.lower() for key in keys}

    def redact_text(self, text: str) -> RedactionResult:
        original = str(text)
        redacted = original
        total = 0
        categories: list[str] = []

        for category, pattern, replacement in self._TEXT_PATTERNS:
            redacted, count = pattern.subn(replacement, redacted)
            if count > 0:
                categories.append(category)
                total += count

        return RedactionResult(
            original_text=original,
            redacted_text=redacted,
            redaction_count=total,
            categories=tuple(categories),
        )

    def redact_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}

        for key, value in metadata.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in self._sensitive_metadata_keys:
                sanitized[key] = f"<REDACTED:{normalized_key.upper()}>"
                continue

            if isinstance(value, str):
                sanitized[key] = self.redact_text(value).redacted_text
            elif isinstance(value, (list, tuple)):
                sanitized[key] = [self.redact_text(str(item)).redacted_text for item in value]
            else:
                sanitized[key] = value

        return sanitized


__all__ = ["MemoryPrivacyFilter", "RedactionResult"]
