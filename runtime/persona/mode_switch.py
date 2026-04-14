"""Operational mode switch manager for FRIDAY and JARVIS workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4


@dataclass(frozen=True)
class ModePolicy:
    mode: str
    label: str
    response_tone: str
    max_parallel_tasks: int
    requires_confirmation: bool


@dataclass(frozen=True)
class ModeTransition:
    transition_id: str
    from_mode: str
    to_mode: str
    reason: str
    actor_id: str
    changed: bool
    created_at: str


class ModeSwitchManager:
    """Tracks current operational mode and validated mode transitions."""

    def __init__(self, default_mode: str = "standard", max_history: int = 100) -> None:
        if max_history < 1:
            raise ValueError("max_history must be at least 1")

        self._policies = _default_mode_policies()
        normalized_default = self._normalize_mode(default_mode)
        if normalized_default not in self._policies:
            supported = ", ".join(sorted(self._policies.keys()))
            raise ValueError(f"Unsupported default mode: {default_mode}. Available: {supported}")

        self._current_mode = normalized_default
        self._history: list[ModeTransition] = []
        self.max_history = max_history

    @property
    def current_mode(self) -> str:
        return self._current_mode

    def list_modes(self) -> list[str]:
        return sorted(self._policies.keys())

    def get_policy(self, mode: str | None = None) -> ModePolicy:
        target_mode = self._current_mode if mode is None else self._normalize_mode(mode)
        policy = self._policies.get(target_mode)
        if policy is None:
            supported = ", ".join(sorted(self._policies.keys()))
            raise ValueError(f"Unsupported mode: {mode}. Available: {supported}")
        return policy

    def switch_mode(self, mode: str, *, reason: str, actor_id: str = "boss") -> ModeTransition:
        normalized_mode = self._normalize_mode(mode)
        if normalized_mode not in self._policies:
            supported = ", ".join(sorted(self._policies.keys()))
            raise ValueError(f"Unsupported mode: {mode}. Available: {supported}")

        normalized_reason = " ".join(reason.split())
        if not normalized_reason:
            raise ValueError("reason is required")

        normalized_actor = " ".join(actor_id.split())
        if not normalized_actor:
            raise ValueError("actor_id is required")

        from_mode = self._current_mode
        changed = from_mode != normalized_mode
        if changed:
            self._current_mode = normalized_mode

        transition = ModeTransition(
            transition_id=str(uuid4()),
            from_mode=from_mode,
            to_mode=normalized_mode,
            reason=normalized_reason,
            actor_id=normalized_actor,
            changed=changed,
            created_at=_utc_now_iso(),
        )
        self._history.append(transition)
        self._trim_history()
        return transition

    def history(self, limit: int = 20) -> list[ModeTransition]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        return list(reversed(self._history[-limit:]))

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        return "_".join(mode.lower().split())

    def _trim_history(self) -> None:
        overflow = len(self._history) - self.max_history
        if overflow > 0:
            del self._history[:overflow]


def _default_mode_policies() -> dict[str, ModePolicy]:
    return {
        "standard": ModePolicy(
            mode="standard",
            label="Standard",
            response_tone="balanced",
            max_parallel_tasks=2,
            requires_confirmation=False,
        ),
        "war_room": ModePolicy(
            mode="war_room",
            label="War Room",
            response_tone="decisive",
            max_parallel_tasks=6,
            requires_confirmation=True,
        ),
        "deep_research": ModePolicy(
            mode="deep_research",
            label="Deep Research",
            response_tone="analytical",
            max_parallel_tasks=4,
            requires_confirmation=False,
        ),
        "stealth": ModePolicy(
            mode="stealth",
            label="Stealth",
            response_tone="minimal",
            max_parallel_tasks=1,
            requires_confirmation=True,
        ),
        "creative": ModePolicy(
            mode="creative",
            label="Creative",
            response_tone="exploratory",
            max_parallel_tasks=3,
            requires_confirmation=False,
        ),
        "mission_brief": ModePolicy(
            mode="mission_brief",
            label="Mission Brief",
            response_tone="structured",
            max_parallel_tasks=2,
            requires_confirmation=True,
        ),
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
