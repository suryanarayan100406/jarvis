"""Addressing preference resolution with role-based and operator-specific overrides."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .profile_engine import PersonaProfile


@dataclass(frozen=True)
class AddressingResolution:
    address: str
    source: str
    mode: str


class AddressingPreferenceLayer:
    """Resolves operator addressing with mode and role-aware precedence rules."""

    def __init__(
        self,
        mode_defaults: Mapping[str, str] | None = None,
        role_overrides: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        defaults = mode_defaults or {
            "friday": "Boss",
            "jarvis": "Sir or Maam",
        }
        self.mode_defaults = {self._normalize_mode(mode): value for mode, value in defaults.items() if value.strip()}

        incoming_role_overrides = role_overrides or {
            "friday": {
                "primary_user": "Boss",
                "administrator": "Commander",
                "limited_user": "Operator",
            },
            "jarvis": {
                "primary_user": "Sir",
                "administrator": "Director",
                "limited_user": "Operator",
            },
        }
        self.role_overrides = {
            self._normalize_mode(mode): {role: value for role, value in mapping.items() if value.strip()}
            for mode, mapping in incoming_role_overrides.items()
        }

    def resolve(
        self,
        *,
        operator_id: str,
        operator_role: str,
        mode: str = "friday",
        operator_overrides: Mapping[str, str] | None = None,
        jarvis_honorific: str | None = None,
    ) -> AddressingResolution:
        normalized_mode = self._normalize_mode(mode)
        if normalized_mode not in self.mode_defaults:
            supported = ", ".join(sorted(self.mode_defaults.keys()))
            raise ValueError(f"Unsupported mode: {mode}. Available: {supported}")

        if operator_overrides:
            override = operator_overrides.get(operator_id)
            if isinstance(override, str) and override.strip():
                return AddressingResolution(address=" ".join(override.split()), source="operator_override", mode=normalized_mode)

        if normalized_mode == "jarvis" and jarvis_honorific in {"Sir", "Maam"}:
            return AddressingResolution(address=jarvis_honorific, source="jarvis_honorific", mode=normalized_mode)

        role_map = self.role_overrides.get(normalized_mode, {})
        role_address = role_map.get(operator_role)
        if role_address:
            return AddressingResolution(address=role_address, source="role_override", mode=normalized_mode)

        return AddressingResolution(
            address=self.mode_defaults[normalized_mode],
            source="mode_default",
            mode=normalized_mode,
        )

    def resolve_for_profile(
        self,
        *,
        profile: PersonaProfile,
        operator_id: str,
        operator_role: str,
        operator_overrides: Mapping[str, str] | None = None,
        jarvis_honorific: str | None = None,
    ) -> AddressingResolution:
        normalized_mode = self._normalize_mode(profile.profile_id)
        if normalized_mode in self.mode_defaults:
            return self.resolve(
                operator_id=operator_id,
                operator_role=operator_role,
                mode=normalized_mode,
                operator_overrides=operator_overrides,
                jarvis_honorific=jarvis_honorific,
            )

        if operator_overrides:
            override = operator_overrides.get(operator_id)
            if isinstance(override, str) and override.strip():
                return AddressingResolution(address=" ".join(override.split()), source="operator_override", mode=normalized_mode)

        return AddressingResolution(
            address=profile.addressing_default,
            source="profile_default",
            mode=normalized_mode,
        )

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        return "".join(mode.lower().split())
