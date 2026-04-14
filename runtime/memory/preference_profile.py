"""Preference memory workflow for communication style and domain focus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .domain_model import PreferenceMemoryStore

_ALLOWED_TONES = {"direct", "concise", "balanced", "diplomatic", "analytical"}
_ALLOWED_VERBOSITY = {"brief", "standard", "detailed"}
_ALLOWED_RESPONSE_STYLE = {"answer_first", "stepwise", "narrative"}
_ALLOWED_FOCUS_DEPTH = {"overview", "balanced", "deep"}


@dataclass(frozen=True)
class CommunicationStylePreference:
    tone: str
    verbosity: str
    response_style: str
    tone_source: str
    verbosity_source: str
    response_style_source: str


@dataclass(frozen=True)
class DomainFocusPreference:
    topics: tuple[str, ...]
    depth: str
    topics_source: str
    depth_source: str


@dataclass(frozen=True)
class ResolvedPreferenceProfile:
    subject_id: str
    communication: CommunicationStylePreference
    domain_focus: DomainFocusPreference
    resolved_at: str


class PreferenceProfileError(ValueError):
    """Raised when preference profile operations receive invalid inputs."""


class PreferenceProfileMemory:
    """Stores and resolves communication-style and domain-focus preferences."""

    def __init__(self, store: PreferenceMemoryStore) -> None:
        self.store = store

    def set_communication_style(
        self,
        *,
        subject_id: str,
        tone: str | None = None,
        verbosity: str | None = None,
        response_style: str | None = None,
        priority: int = 50,
    ) -> tuple[str, ...]:
        normalized_subject = _normalize_required(subject_id, "subject_id")
        written: list[str] = []

        if tone is None and verbosity is None and response_style is None:
            raise PreferenceProfileError(
                "At least one communication style field must be provided"
            )

        if tone is not None:
            normalized_tone = _normalize_choice(tone, "tone", _ALLOWED_TONES)
            self.store.set_preference(
                subject_id=normalized_subject,
                category="communication_style",
                key="tone",
                value=normalized_tone,
                priority=priority,
                metadata={"scope": "communication_style"},
            )
            written.append("tone")

        if verbosity is not None:
            normalized_verbosity = _normalize_choice(verbosity, "verbosity", _ALLOWED_VERBOSITY)
            self.store.set_preference(
                subject_id=normalized_subject,
                category="communication_style",
                key="verbosity",
                value=normalized_verbosity,
                priority=priority,
                metadata={"scope": "communication_style"},
            )
            written.append("verbosity")

        if response_style is not None:
            normalized_response_style = _normalize_choice(
                response_style,
                "response_style",
                _ALLOWED_RESPONSE_STYLE,
            )
            self.store.set_preference(
                subject_id=normalized_subject,
                category="communication_style",
                key="response_style",
                value=normalized_response_style,
                priority=priority,
                metadata={"scope": "communication_style"},
            )
            written.append("response_style")

        return tuple(written)

    def set_domain_focus(
        self,
        *,
        subject_id: str,
        topics: list[str] | tuple[str, ...] | None = None,
        depth: str | None = None,
        priority: int = 50,
    ) -> tuple[str, ...]:
        normalized_subject = _normalize_required(subject_id, "subject_id")
        written: list[str] = []

        if topics is None and depth is None:
            raise PreferenceProfileError("At least one domain focus field must be provided")

        if topics is not None:
            normalized_topics = _normalize_topics(topics)
            self.store.set_preference(
                subject_id=normalized_subject,
                category="domain_focus",
                key="topics",
                value=normalized_topics,
                priority=priority,
                metadata={"scope": "domain_focus"},
            )
            written.append("topics")

        if depth is not None:
            normalized_depth = _normalize_choice(depth, "depth", _ALLOWED_FOCUS_DEPTH)
            self.store.set_preference(
                subject_id=normalized_subject,
                category="domain_focus",
                key="depth",
                value=normalized_depth,
                priority=priority,
                metadata={"scope": "domain_focus"},
            )
            written.append("depth")

        return tuple(written)

    def resolve_profile(
        self,
        *,
        subject_id: str,
        fallback_subjects: list[str] | tuple[str, ...] | None = None,
    ) -> ResolvedPreferenceProfile:
        normalized_subject = _normalize_required(subject_id, "subject_id")
        fallbacks = tuple(fallback_subjects or ("*",))

        tone_item = self.store.resolve_preference(
            subject_id=normalized_subject,
            category="communication_style",
            key="tone",
            fallback_subjects=fallbacks,
        )
        verbosity_item = self.store.resolve_preference(
            subject_id=normalized_subject,
            category="communication_style",
            key="verbosity",
            fallback_subjects=fallbacks,
        )
        style_item = self.store.resolve_preference(
            subject_id=normalized_subject,
            category="communication_style",
            key="response_style",
            fallback_subjects=fallbacks,
        )

        topics_item = self.store.resolve_preference(
            subject_id=normalized_subject,
            category="domain_focus",
            key="topics",
            fallback_subjects=fallbacks,
        )
        depth_item = self.store.resolve_preference(
            subject_id=normalized_subject,
            category="domain_focus",
            key="depth",
            fallback_subjects=fallbacks,
        )

        tone = _normalize_choice(str(tone_item.value), "tone", _ALLOWED_TONES) if tone_item else "balanced"
        verbosity = (
            _normalize_choice(str(verbosity_item.value), "verbosity", _ALLOWED_VERBOSITY)
            if verbosity_item
            else "standard"
        )
        response_style = (
            _normalize_choice(str(style_item.value), "response_style", _ALLOWED_RESPONSE_STYLE)
            if style_item
            else "answer_first"
        )

        topics = _normalize_topics(topics_item.value) if topics_item else ()
        depth = _normalize_choice(str(depth_item.value), "depth", _ALLOWED_FOCUS_DEPTH) if depth_item else "balanced"

        return ResolvedPreferenceProfile(
            subject_id=normalized_subject,
            communication=CommunicationStylePreference(
                tone=tone,
                verbosity=verbosity,
                response_style=response_style,
                tone_source=tone_item.subject_id if tone_item else "default",
                verbosity_source=verbosity_item.subject_id if verbosity_item else "default",
                response_style_source=style_item.subject_id if style_item else "default",
            ),
            domain_focus=DomainFocusPreference(
                topics=topics,
                depth=depth,
                topics_source=topics_item.subject_id if topics_item else "default",
                depth_source=depth_item.subject_id if depth_item else "default",
            ),
            resolved_at=_utc_now_iso(),
        )


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise PreferenceProfileError(f"{field_name} is required")
    return normalized


def _normalize_choice(value: str, field_name: str, allowed: set[str]) -> str:
    normalized = _normalize_required(value, field_name).lower()
    if normalized not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise PreferenceProfileError(f"Unsupported {field_name}: {value}. Allowed: {allowed_values}")
    return normalized


def _normalize_topics(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        # Single topic is allowed but normalized to tuple for deterministic storage.
        value = [value]

    if not isinstance(value, (list, tuple)):
        raise PreferenceProfileError("topics must be a list or tuple of strings")

    normalized = sorted({_normalize_required(str(topic), "topic").lower() for topic in value})
    return tuple(normalized)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "CommunicationStylePreference",
    "DomainFocusPreference",
    "PreferenceProfileError",
    "PreferenceProfileMemory",
    "ResolvedPreferenceProfile",
]
