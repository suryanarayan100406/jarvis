"""Answer-first response formatter with confidence tagging."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .profile_engine import PersonaProfile

ConfidenceLabel = Literal["high", "medium", "low", "unknown"]


@dataclass(frozen=True)
class FormattedResponse:
    text: str
    answer: str
    confidence: float | None
    confidence_label: ConfidenceLabel
    addressed_to: str
    persona_id: str | None
    tags: tuple[str, ...]


class ResponseFormatter:
    """Formats response text with answer-first ordering and confidence tags."""

    def __init__(self, *, high_threshold: float = 0.85, medium_threshold: float = 0.60) -> None:
        if high_threshold < 0 or high_threshold > 1:
            raise ValueError("high_threshold must be in range [0, 1]")
        if medium_threshold < 0 or medium_threshold > high_threshold:
            raise ValueError("medium_threshold must be in range [0, high_threshold]")

        self.high_threshold = high_threshold
        self.medium_threshold = medium_threshold

    def format_response(
        self,
        answer: str,
        *,
        addressed_to: str = "Boss",
        confidence: float | None = None,
        details: str | None = None,
        include_details: bool = False,
        persona_id: str | None = None,
    ) -> FormattedResponse:
        normalized_answer = _normalize_text(answer)
        normalized_answer = _strip_leading_filler(normalized_answer)
        if not _has_substantive_text(normalized_answer):
            raise ValueError("answer cannot be empty")

        normalized_address = _normalize_text(addressed_to)
        normalized_details = _normalize_text(details or "")
        confidence_value = _normalize_confidence(confidence)
        confidence_label = self._label_for_confidence(confidence_value)

        prefix = f"{normalized_address}, " if normalized_address else ""
        first_line = f"{prefix}{normalized_answer}"
        text = f"{first_line} [confidence:{confidence_label}]"

        if include_details and normalized_details:
            text = f"{text}\nDetails: {normalized_details}"

        tags = ["answer-first", f"confidence:{confidence_label}"]
        if persona_id:
            tags.append(f"persona:{persona_id}")

        return FormattedResponse(
            text=text,
            answer=normalized_answer,
            confidence=confidence_value,
            confidence_label=confidence_label,
            addressed_to=normalized_address,
            persona_id=persona_id,
            tags=tuple(tags),
        )

    def format_with_profile(
        self,
        profile: PersonaProfile,
        answer: str,
        *,
        addressed_to: str | None = None,
        confidence: float | None = None,
        details: str | None = None,
        include_details: bool = False,
    ) -> FormattedResponse:
        resolved_address = addressed_to or profile.addressing_default
        return self.format_response(
            answer,
            addressed_to=resolved_address,
            confidence=confidence,
            details=details,
            include_details=include_details,
            persona_id=profile.profile_id,
        )

    def _label_for_confidence(self, confidence: float | None) -> ConfidenceLabel:
        if confidence is None:
            return "unknown"
        if confidence >= self.high_threshold:
            return "high"
        if confidence >= self.medium_threshold:
            return "medium"
        return "low"


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _strip_leading_filler(value: str) -> str:
    stripped = value
    while True:
        updated = _LEADING_FILLER_PATTERN.sub("", stripped, count=1).strip()
        if updated == stripped:
            break
        stripped = updated
    return stripped.lstrip(" ,;:.-")


def _has_substantive_text(value: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9]", value))


def _normalize_confidence(confidence: float | None) -> float | None:
    if confidence is None:
        return None

    bounded = max(0.0, min(1.0, float(confidence)))
    return round(bounded, 3)


_LEADING_FILLER_PATTERN = re.compile(
    r"^(?:sure|certainly|absolutely|of course|no problem|gladly|okay|ok|alright)\b[\s,;:!\-]*",
    re.IGNORECASE,
)
