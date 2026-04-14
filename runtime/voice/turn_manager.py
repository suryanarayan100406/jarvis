"""Conversational turn manager with interruption-aware state transitions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

TurnStatus = Literal["listening", "responding", "interrupted", "completed", "cancelled"]


@dataclass(frozen=True)
class TurnInterruption:
    interruption_id: str
    reason: str
    utterance: str
    created_at: str


@dataclass(frozen=True)
class ConversationTurn:
    turn_id: str
    user_input: str
    source: str
    status: TurnStatus
    response_text: str
    interruptions: tuple[TurnInterruption, ...]
    created_at: str
    updated_at: str


class TurnStateError(RuntimeError):
    """Raised when a turn operation violates state constraints."""


class ConversationTurnManager:
    """Tracks conversational turns and interruption-aware response lifecycle."""

    def __init__(self, max_history: int = 200) -> None:
        if max_history < 1:
            raise ValueError("max_history must be at least 1")

        self.max_history = max_history
        self._turns: dict[str, ConversationTurn] = {}
        self._timeline: list[str] = []
        self._active_turn_id: str | None = None

    def start_turn(self, user_input: str, source: str = "voice") -> ConversationTurn:
        normalized = _normalize_text(user_input)
        if not normalized:
            raise ValueError("user_input cannot be empty")

        if self._active_turn_id is not None:
            self.interrupt(self._active_turn_id, reason="new_turn_started", utterance=normalized)

        now = _utc_now_iso()
        turn = ConversationTurn(
            turn_id=str(uuid4()),
            user_input=normalized,
            source=source,
            status="listening",
            response_text="",
            interruptions=(),
            created_at=now,
            updated_at=now,
        )

        self._turns[turn.turn_id] = turn
        self._timeline.append(turn.turn_id)
        self._active_turn_id = turn.turn_id
        self._trim_history()
        return turn

    def begin_response(self, turn_id: str) -> ConversationTurn:
        turn = self._require_turn(turn_id)
        if turn.status not in {"listening", "interrupted"}:
            raise TurnStateError(f"Cannot begin response from state: {turn.status}")

        updated = self._update_turn(turn, status="responding")
        self._active_turn_id = turn_id
        return updated

    def append_response(self, turn_id: str, text: str) -> ConversationTurn:
        turn = self._require_turn(turn_id)
        if turn.status != "responding":
            raise TurnStateError(f"Cannot append response while turn is {turn.status}")

        normalized = _normalize_text(text)
        if not normalized:
            return turn

        combined = normalized if not turn.response_text else f"{turn.response_text} {normalized}"
        return self._update_turn(turn, response_text=combined)

    def interrupt(self, turn_id: str, reason: str, utterance: str = "") -> ConversationTurn:
        turn = self._require_turn(turn_id)
        if turn.status in {"completed", "cancelled"}:
            raise TurnStateError(f"Cannot interrupt turn in terminal state: {turn.status}")

        interruption = TurnInterruption(
            interruption_id=str(uuid4()),
            reason=_normalize_text(reason) or "interruption",
            utterance=_normalize_text(utterance),
            created_at=_utc_now_iso(),
        )
        updated = self._update_turn(
            turn,
            status="interrupted",
            interruptions=turn.interruptions + (interruption,),
        )

        if self._active_turn_id == turn_id:
            self._active_turn_id = None

        return updated

    def resume(self, turn_id: str) -> ConversationTurn:
        turn = self._require_turn(turn_id)
        if turn.status != "interrupted":
            raise TurnStateError(f"Cannot resume turn while it is {turn.status}")

        updated = self._update_turn(turn, status="responding")
        self._active_turn_id = turn_id
        return updated

    def complete(self, turn_id: str) -> ConversationTurn:
        turn = self._require_turn(turn_id)
        if turn.status not in {"listening", "responding", "interrupted"}:
            raise TurnStateError(f"Cannot complete turn from state: {turn.status}")

        updated = self._update_turn(turn, status="completed")
        if self._active_turn_id == turn_id:
            self._active_turn_id = None
        return updated

    def cancel(self, turn_id: str) -> ConversationTurn:
        turn = self._require_turn(turn_id)
        if turn.status in {"completed", "cancelled"}:
            raise TurnStateError(f"Cannot cancel turn from state: {turn.status}")

        updated = self._update_turn(turn, status="cancelled")
        if self._active_turn_id == turn_id:
            self._active_turn_id = None
        return updated

    def get_turn(self, turn_id: str) -> ConversationTurn:
        return self._require_turn(turn_id)

    def get_active_turn(self) -> ConversationTurn | None:
        if self._active_turn_id is None:
            return None
        return self._turns.get(self._active_turn_id)

    def list_recent(self, limit: int = 20) -> list[ConversationTurn]:
        if limit < 1:
            raise ValueError("limit must be at least 1")

        selected_ids = list(reversed(self._timeline))[:limit]
        return [self._turns[turn_id] for turn_id in selected_ids if turn_id in self._turns]

    def _trim_history(self) -> None:
        while len(self._timeline) > self.max_history:
            oldest = self._timeline[0]
            if oldest == self._active_turn_id:
                break

            self._timeline.pop(0)
            self._turns.pop(oldest, None)

    def _require_turn(self, turn_id: str) -> ConversationTurn:
        turn = self._turns.get(turn_id)
        if turn is None:
            raise KeyError(f"Unknown turn: {turn_id}")
        return turn

    def _update_turn(self, turn: ConversationTurn, **updates: object) -> ConversationTurn:
        updated = replace(turn, updated_at=_utc_now_iso(), **updates)
        self._turns[turn.turn_id] = updated
        return updated


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
