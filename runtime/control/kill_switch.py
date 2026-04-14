"""Global kill-switch controller for emergency runtime halts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, RLock
from typing import Callable
from uuid import uuid4


@dataclass
class KillSwitchEvent:
    """Represents a kill-switch state transition and hook execution summary."""

    event_id: str
    timestamp: str
    state: str
    reason: str
    actor: str
    triggered_hooks: list[str] = field(default_factory=list)
    hook_errors: list[str] = field(default_factory=list)


class KillSwitchActivatedError(RuntimeError):
    """Raised when execution is attempted while kill-switch is active."""


class KillSwitchController:
    """Coordinates emergency stop state and halt hooks across runtime components."""

    def __init__(self) -> None:
        self._stop_signal = Event()
        self._lock = RLock()
        self._hooks: dict[str, Callable[[KillSwitchEvent], None]] = {}
        self._history: list[KillSwitchEvent] = []

    def register_halt_hook(self, name: str, callback: Callable[[KillSwitchEvent], None]) -> None:
        """Register a named callback that runs when kill-switch is activated."""
        if not name or not isinstance(name, str):
            raise ValueError("Hook name must be a non-empty string")
        if not callable(callback):
            raise TypeError("Hook callback must be callable")

        with self._lock:
            if name in self._hooks:
                raise ValueError(f"Hook already registered: {name}")
            self._hooks[name] = callback

    def activate(self, reason: str, actor: str = "system") -> KillSwitchEvent:
        """Activate emergency stop and execute all registered halt hooks."""
        if not reason:
            raise ValueError("Activation reason is required")

        with self._lock:
            if self._stop_signal.is_set():
                return self._history[-1]

            event = KillSwitchEvent(
                event_id=str(uuid4()),
                timestamp=_utc_now_iso(),
                state="active",
                reason=reason,
                actor=actor,
            )
            self._stop_signal.set()
            hooks_snapshot = dict(self._hooks)

        for name, callback in hooks_snapshot.items():
            try:
                callback(event)
                event.triggered_hooks.append(name)
            except Exception as exc:  # pragma: no cover - intentionally broad for fail-safe halt path
                event.hook_errors.append(f"{name}: {exc}")

        with self._lock:
            self._history.append(event)

        return event

    def reset(self, reason: str = "manual_reset", actor: str = "system") -> KillSwitchEvent:
        """Clear emergency stop state and emit reset event."""
        with self._lock:
            self._stop_signal.clear()
            event = KillSwitchEvent(
                event_id=str(uuid4()),
                timestamp=_utc_now_iso(),
                state="inactive",
                reason=reason,
                actor=actor,
            )
            self._history.append(event)
            return event

    def is_active(self) -> bool:
        return self._stop_signal.is_set()

    def wait_for_stop(self, timeout: float | None = None) -> bool:
        """Wait for global stop signal. Returns true if signal observed."""
        return self._stop_signal.wait(timeout)

    def assert_can_execute(self) -> None:
        """Raise when kill-switch is active to prevent execution."""
        if self._stop_signal.is_set():
            raise KillSwitchActivatedError("Execution blocked: kill-switch is active")

    def run_guarded(self, func: Callable[..., object], *args: object, **kwargs: object) -> object:
        """Execute callable only when kill-switch is not active."""
        self.assert_can_execute()
        return func(*args, **kwargs)

    @property
    def history(self) -> list[KillSwitchEvent]:
        return list(self._history)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
