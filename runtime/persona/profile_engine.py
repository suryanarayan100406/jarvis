"""Persona profile engine for FRIDAY and JARVIS communication modes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping


@dataclass(frozen=True)
class PersonaProfile:
    profile_id: str
    display_name: str
    addressing_default: str
    tone: tuple[str, ...]
    response_style: str
    confidence_style: str
    safety_posture: tuple[str, ...]


class PersonaProfileEngine:
    """Resolves persona profiles and applies constrained runtime overrides."""

    def __init__(self, profiles: Mapping[str, PersonaProfile] | None = None) -> None:
        self._profiles = dict(profiles) if profiles is not None else _default_profiles()

    def list_profiles(self) -> list[PersonaProfile]:
        return [self._profiles[key] for key in sorted(self._profiles.keys())]

    def select_profile(self, mode: str, overrides: Mapping[str, Any] | None = None) -> PersonaProfile:
        normalized_mode = _normalize_mode(mode)
        profile = self._profiles.get(normalized_mode)
        if profile is None:
            available = ", ".join(sorted(self._profiles.keys()))
            raise ValueError(f"Unsupported persona mode: {mode}. Available: {available}")

        return self._apply_overrides(profile, overrides)

    def build_anchor(self, profile: PersonaProfile) -> dict[str, Any]:
        return {
            "persona_id": profile.profile_id,
            "display_name": profile.display_name,
            "addressing_default": profile.addressing_default,
            "tone": list(profile.tone),
            "response_style": profile.response_style,
            "confidence_style": profile.confidence_style,
            "safety_posture": list(profile.safety_posture),
            "answer_first": True,
        }

    def _apply_overrides(self, profile: PersonaProfile, overrides: Mapping[str, Any] | None) -> PersonaProfile:
        if not overrides:
            return profile

        updated = profile
        addressing_override = overrides.get("addressing_default")
        if isinstance(addressing_override, str) and addressing_override.strip():
            updated = replace(updated, addressing_default=" ".join(addressing_override.split()))

        response_style_override = overrides.get("response_style")
        if isinstance(response_style_override, str) and response_style_override.strip():
            updated = replace(updated, response_style=" ".join(response_style_override.split()))

        tone_override = overrides.get("tone")
        normalized_tone = _normalize_tone(tone_override)
        if normalized_tone is not None:
            updated = replace(updated, tone=normalized_tone)

        return updated


def _default_profiles() -> dict[str, PersonaProfile]:
    return {
        "friday": PersonaProfile(
            profile_id="friday",
            display_name="FRIDAY",
            addressing_default="Boss",
            tone=("decisive", "efficient", "protective"),
            response_style="answer-first concise",
            confidence_style="explicit-confidence-tag",
            safety_posture=("policy-first", "risk-aware", "operator-confirmation-on-escalation"),
        ),
        "jarvis": PersonaProfile(
            profile_id="jarvis",
            display_name="JARVIS",
            addressing_default="Sir or Maam",
            tone=("polite", "precise", "composed"),
            response_style="answer-first formal",
            confidence_style="explicit-confidence-tag",
            safety_posture=("policy-first", "risk-aware", "de-escalation-priority"),
        ),
    }


def _normalize_mode(mode: str) -> str:
    normalized = "".join(mode.lower().split())
    if normalized == "jarvis":
        return "jarvis"
    if normalized == "friday":
        return "friday"
    return normalized


def _normalize_tone(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None

    if isinstance(value, str):
        tokens = [token.strip() for token in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        tokens = [str(token).strip() for token in value]
    else:
        return None

    normalized = tuple(token for token in tokens if token)
    return normalized or None
