"""Run replay endpoint for debugging and audit workflows."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Iterable

from runtime.store import LocalRunStore, RunEvent, RunRecord


@dataclass(frozen=True)
class ReplayEventRecord:
    event_id: int
    event_type: str
    severity: str
    created_at: str
    payload: dict[str, Any]
    offset_ms: int


@dataclass(frozen=True)
class RunReplayResult:
    run: RunRecord
    events: list[ReplayEventRecord]
    metadata: dict[str, Any]


class RunReplayNotFoundError(KeyError):
    """Raised when a run cannot be replayed because it does not exist."""


class RunReplayEndpoint:
    """Reads persisted run history and returns replayable timeline records."""

    def __init__(self, store: LocalRunStore, default_limit: int = 500) -> None:
        if default_limit < 1:
            raise ValueError("default_limit must be at least 1")

        self.store = store
        self.default_limit = default_limit

    def replay(
        self,
        run_id: str,
        *,
        limit: int | None = None,
        event_types: Iterable[str] | None = None,
        severities: Iterable[str] | None = None,
        include_payload: bool = True,
    ) -> RunReplayResult:
        if not run_id or not run_id.strip():
            raise ValueError("run_id is required")
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")

        effective_limit = self.default_limit if limit is None else limit
        run = self._get_run_or_raise(run_id)

        all_events = self.store.list_events(run_id, limit=None)
        total_event_count = len(all_events)

        filtered = self._filter_events(all_events, event_types=event_types, severities=severities)
        filtered_event_count = len(filtered)

        truncated = filtered_event_count > effective_limit
        selected = filtered[:effective_limit]
        replay_events = self._build_replay_events(selected, include_payload=include_payload)

        metadata = {
            "replayed_at": _utc_now_iso(),
            "total_event_count": total_event_count,
            "filtered_event_count": filtered_event_count,
            "returned_event_count": len(replay_events),
            "truncated": truncated,
            "next_cursor_event_id": replay_events[-1].event_id if truncated and replay_events else None,
            "include_payload": include_payload,
            "audit_digest": self._compute_audit_digest(run, replay_events),
        }

        return RunReplayResult(run=run, events=replay_events, metadata=metadata)

    def _get_run_or_raise(self, run_id: str) -> RunRecord:
        try:
            return self.store.get_run(run_id)
        except KeyError as exc:
            raise RunReplayNotFoundError(f"Run not found: {run_id}") from exc

    def _filter_events(
        self,
        events: list[RunEvent],
        event_types: Iterable[str] | None,
        severities: Iterable[str] | None,
    ) -> list[RunEvent]:
        event_type_filter = _normalize_filter(event_types)
        severity_filter = _normalize_filter(severities)

        filtered: list[RunEvent] = []
        for event in events:
            if event_type_filter is not None and event.event_type not in event_type_filter:
                continue
            if severity_filter is not None and event.severity not in severity_filter:
                continue
            filtered.append(event)

        return filtered

    def _build_replay_events(self, events: list[RunEvent], include_payload: bool) -> list[ReplayEventRecord]:
        if not events:
            return []

        baseline = _parse_iso_utc(events[0].created_at)

        replayed: list[ReplayEventRecord] = []
        for event in events:
            created_at = _parse_iso_utc(event.created_at)
            payload = dict(event.payload) if include_payload else {"redacted": True}
            replayed.append(
                ReplayEventRecord(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    severity=event.severity,
                    created_at=event.created_at,
                    payload=payload,
                    offset_ms=max(0, int((created_at - baseline).total_seconds() * 1000)),
                )
            )

        return replayed

    def _compute_audit_digest(self, run: RunRecord, events: list[ReplayEventRecord]) -> str:
        canonical = {
            "run": asdict(run),
            "events": [asdict(event) for event in events],
        }
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


def _normalize_filter(values: Iterable[str] | None) -> set[str] | None:
    if values is None:
        return None

    normalized = {value.strip() for value in values if value and value.strip()}
    return normalized or None


def _parse_iso_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
