"""Deterministic run state machine for FRIDAY orchestration runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

RunStage = Literal[
    "plan",
    "execute",
    "validate",
    "report",
    "completed",
    "failed",
    "cancelled",
]

ALLOWED_TRANSITIONS: dict[RunStage, tuple[RunStage, ...]] = {
    "plan": ("execute", "failed", "cancelled"),
    "execute": ("validate", "failed", "cancelled"),
    "validate": ("report", "failed", "cancelled"),
    "report": ("completed", "failed"),
    "completed": (),
    "failed": (),
    "cancelled": (),
}

SUCCESSOR_TRANSITION: dict[RunStage, RunStage] = {
    "plan": "execute",
    "execute": "validate",
    "validate": "report",
    "report": "completed",
}


@dataclass(frozen=True)
class TransitionRecord:
    """Represents a single run-stage transition."""

    run_id: str
    from_stage: RunStage
    to_stage: RunStage
    reason: str
    timestamp: str


class InvalidRunTransitionError(ValueError):
    """Raised when an invalid state transition is attempted."""


class RunStateMachine:
    """Enforces deterministic orchestration state transitions."""

    def __init__(self, run_id: str | None = None, initial_stage: RunStage = "plan") -> None:
        if initial_stage not in ALLOWED_TRANSITIONS:
            raise ValueError(f"Invalid initial stage: {initial_stage}")

        self.run_id = run_id or str(uuid4())
        self.current_stage: RunStage = initial_stage
        self._history: list[TransitionRecord] = []

    def can_transition(self, to_stage: RunStage) -> bool:
        return to_stage in ALLOWED_TRANSITIONS[self.current_stage]

    def transition_to(self, to_stage: RunStage, reason: str = "") -> TransitionRecord:
        if to_stage not in ALLOWED_TRANSITIONS:
            raise ValueError(f"Unknown stage: {to_stage}")
        if not self.can_transition(to_stage):
            allowed = ", ".join(ALLOWED_TRANSITIONS[self.current_stage]) or "<none>"
            raise InvalidRunTransitionError(
                f"Invalid transition from {self.current_stage} to {to_stage}. Allowed: {allowed}"
            )

        record = TransitionRecord(
            run_id=self.run_id,
            from_stage=self.current_stage,
            to_stage=to_stage,
            reason=reason,
            timestamp=_utc_now_iso(),
        )
        self.current_stage = to_stage
        self._history.append(record)
        return record

    def advance_success(self, reason: str = "success_path") -> TransitionRecord:
        """Advance one step along the standard success path."""
        if self.current_stage not in SUCCESSOR_TRANSITION:
            raise InvalidRunTransitionError(
                f"No success-path transition available from terminal stage {self.current_stage}"
            )
        return self.transition_to(SUCCESSOR_TRANSITION[self.current_stage], reason=reason)

    @property
    def history(self) -> list[TransitionRecord]:
        return list(self._history)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
