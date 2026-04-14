"""Watchdog for stuck run detection and auto-restart logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

RunWatchStatus = Literal["active", "restarting", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class WatchedRun:
    run_id: str
    status: RunWatchStatus
    last_progress_at: str
    restart_attempts: int
    max_restarts: int
    last_restart_at: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WatchdogAction:
    run_id: str
    action: str
    reason: str
    restart_attempt: int
    created_at: str


@dataclass(frozen=True)
class WatchdogScanResult:
    scanned: int
    stuck: int
    restarted: int
    terminalized: int
    actions: tuple[WatchdogAction, ...]
    scanned_at: str


class WatchdogError(ValueError):
    """Raised when watchdog operations receive invalid inputs."""


class RunWatchdog:
    """Monitors run progress and schedules bounded restarts for stuck runs."""

    def __init__(self, *, stuck_timeout_seconds: int = 300, restart_cooldown_seconds: int = 30) -> None:
        if stuck_timeout_seconds < 1:
            raise WatchdogError("stuck_timeout_seconds must be at least 1")
        if restart_cooldown_seconds < 0:
            raise WatchdogError("restart_cooldown_seconds must be non-negative")

        self.stuck_timeout_seconds = stuck_timeout_seconds
        self.restart_cooldown_seconds = restart_cooldown_seconds
        self._runs: dict[str, WatchedRun] = {}

    def track_run(
        self,
        *,
        run_id: str,
        max_restarts: int = 2,
        metadata: dict[str, Any] | None = None,
        last_progress_at: str | None = None,
    ) -> WatchedRun:
        normalized_id = _normalize_required(run_id, "run_id")
        if max_restarts < 0:
            raise WatchdogError("max_restarts must be non-negative")

        now = _utc_now()
        progress_time = _parse_iso(last_progress_at) if last_progress_at is not None else now
        now_iso = _to_iso(now)

        watched = WatchedRun(
            run_id=normalized_id,
            status="active",
            last_progress_at=_to_iso(progress_time),
            restart_attempts=0,
            max_restarts=max_restarts,
            last_restart_at=None,
            metadata=dict(metadata or {}),
            created_at=now_iso,
            updated_at=now_iso,
        )
        self._runs[normalized_id] = watched
        return watched

    def get_run(self, run_id: str) -> WatchedRun:
        normalized_id = _normalize_required(run_id, "run_id")
        watched = self._runs.get(normalized_id)
        if watched is None:
            raise KeyError(f"Unknown run_id: {normalized_id}")
        return watched

    def heartbeat(self, run_id: str, *, at: str | None = None) -> WatchedRun:
        watched = self.get_run(run_id)
        if watched.status in {"completed", "failed", "cancelled"}:
            return watched

        heartbeat_time = _parse_iso(at) if at is not None else _utc_now()
        updated = WatchedRun(
            run_id=watched.run_id,
            status="active",
            last_progress_at=_to_iso(heartbeat_time),
            restart_attempts=watched.restart_attempts,
            max_restarts=watched.max_restarts,
            last_restart_at=watched.last_restart_at,
            metadata=dict(watched.metadata),
            created_at=watched.created_at,
            updated_at=_utc_now_iso(),
        )
        self._runs[watched.run_id] = updated
        return updated

    def mark_terminal(self, run_id: str, *, status: RunWatchStatus) -> WatchedRun:
        if status not in {"completed", "failed", "cancelled"}:
            raise WatchdogError("status must be completed, failed, or cancelled")

        watched = self.get_run(run_id)
        updated = WatchedRun(
            run_id=watched.run_id,
            status=status,
            last_progress_at=watched.last_progress_at,
            restart_attempts=watched.restart_attempts,
            max_restarts=watched.max_restarts,
            last_restart_at=watched.last_restart_at,
            metadata=dict(watched.metadata),
            created_at=watched.created_at,
            updated_at=_utc_now_iso(),
        )
        self._runs[watched.run_id] = updated
        return updated

    def list_runs(self) -> list[WatchedRun]:
        runs = list(self._runs.values())
        runs.sort(key=lambda item: item.run_id)
        return runs

    def scan(self, *, reference_time: str | None = None) -> WatchdogScanResult:
        now = _parse_iso(reference_time) if reference_time is not None else _utc_now()
        now_iso = _to_iso(now)
        actions: list[WatchdogAction] = []
        scanned = 0
        stuck = 0
        restarted = 0
        terminalized = 0

        for watched in self.list_runs():
            if watched.status not in {"active", "restarting"}:
                continue
            scanned += 1

            elapsed = now - _parse_iso(watched.last_progress_at)
            if elapsed <= timedelta(seconds=self.stuck_timeout_seconds):
                continue

            stuck += 1
            can_restart = watched.restart_attempts < watched.max_restarts
            cooldown_satisfied = self._cooldown_satisfied(watched, now)

            if can_restart and cooldown_satisfied:
                updated = WatchedRun(
                    run_id=watched.run_id,
                    status="restarting",
                    last_progress_at=now_iso,
                    restart_attempts=watched.restart_attempts + 1,
                    max_restarts=watched.max_restarts,
                    last_restart_at=now_iso,
                    metadata=dict(watched.metadata),
                    created_at=watched.created_at,
                    updated_at=now_iso,
                )
                self._runs[watched.run_id] = updated
                restarted += 1
                actions.append(
                    WatchdogAction(
                        run_id=watched.run_id,
                        action="restart",
                        reason="stuck_timeout",
                        restart_attempt=updated.restart_attempts,
                        created_at=now_iso,
                    )
                )
                continue

            updated = WatchedRun(
                run_id=watched.run_id,
                status="failed",
                last_progress_at=watched.last_progress_at,
                restart_attempts=watched.restart_attempts,
                max_restarts=watched.max_restarts,
                last_restart_at=watched.last_restart_at,
                metadata=dict(watched.metadata),
                created_at=watched.created_at,
                updated_at=now_iso,
            )
            self._runs[watched.run_id] = updated
            terminalized += 1
            actions.append(
                WatchdogAction(
                    run_id=watched.run_id,
                    action="mark_failed",
                    reason="restart_budget_exhausted" if not can_restart else "restart_cooldown_active",
                    restart_attempt=watched.restart_attempts,
                    created_at=now_iso,
                )
            )

        return WatchdogScanResult(
            scanned=scanned,
            stuck=stuck,
            restarted=restarted,
            terminalized=terminalized,
            actions=tuple(actions),
            scanned_at=now_iso,
        )

    def _cooldown_satisfied(self, watched: WatchedRun, now: datetime) -> bool:
        if self.restart_cooldown_seconds == 0 or watched.last_restart_at is None:
            return True
        elapsed = now - _parse_iso(watched.last_restart_at)
        return elapsed >= timedelta(seconds=self.restart_cooldown_seconds)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise WatchdogError(f"{field_name} is required")
    return normalized


def _parse_iso(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _to_iso(_utc_now())


__all__ = [
    "RunWatchStatus",
    "RunWatchdog",
    "WatchedRun",
    "WatchdogAction",
    "WatchdogError",
    "WatchdogScanResult",
]
