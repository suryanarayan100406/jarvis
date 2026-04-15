"""Previous-session carry-over summary workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from runtime.memory import OpenLoopTask, OpenLoopTaskRegister, StatusCheckCommand, StatusCheckResponse


@dataclass(frozen=True)
class SessionCarryOverItem:
    item_id: str
    task_id: str
    title: str
    priority: str
    status: str
    owner_id: str
    due_at: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SessionCarryOverSummary:
    summary_id: str
    previous_session_id: str
    generated_at: str
    owner_scope: str | None
    open_loop_count: int
    in_progress_count: int
    blocked_count: int
    critical_count: int
    overdue_count: int
    carry_over_items: tuple[SessionCarryOverItem, ...]
    context_notes: tuple[str, ...]
    status_check_summary: str
    summary_text: str
    deterministic_digest: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "previous_session_id": self.previous_session_id,
            "generated_at": self.generated_at,
            "owner_scope": self.owner_scope,
            "open_loop_count": self.open_loop_count,
            "in_progress_count": self.in_progress_count,
            "blocked_count": self.blocked_count,
            "critical_count": self.critical_count,
            "overdue_count": self.overdue_count,
            "carry_over_items": [
                {
                    "item_id": item.item_id,
                    "task_id": item.task_id,
                    "title": item.title,
                    "priority": item.priority,
                    "status": item.status,
                    "owner_id": item.owner_id,
                    "due_at": item.due_at,
                    "metadata": dict(item.metadata),
                }
                for item in sorted(self.carry_over_items, key=lambda entry: entry.item_id)
            ],
            "context_notes": list(self.context_notes),
            "status_check_summary": self.status_check_summary,
            "summary_text": self.summary_text,
            "deterministic_digest": self.deterministic_digest,
            "metadata": dict(self.metadata),
        }


class SessionCarryOverError(ValueError):
    """Raised when carry-over summary inputs are invalid."""


class PreviousSessionCarryOverWorkflow:
    """Builds carry-over summaries for resumed sessions."""

    def __init__(self, register: OpenLoopTaskRegister) -> None:
        if not isinstance(register, OpenLoopTaskRegister):
            raise TypeError("register must be OpenLoopTaskRegister")
        self._register = register

    def build_summary(
        self,
        *,
        previous_session_id: str,
        owner_id: str | None = None,
        limit: int = 5,
        context_notes: list[str] | tuple[str, ...] | None = None,
        reference_time: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionCarryOverSummary:
        normalized_session_id = _normalize_required(previous_session_id, "previous_session_id")
        normalized_owner = _normalize_optional(owner_id)

        if not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit < 1:
            raise SessionCarryOverError("limit must be at least 1")

        normalized_notes = _normalize_notes(context_notes)

        status_response = StatusCheckCommand(self._register).execute(
            actor_id="session-resume",
            owner_id=normalized_owner,
            include_completed=False,
            limit=limit,
            reference_time=reference_time,
        )

        carry_over_items = self._build_items(status_response.tasks, limit=limit)
        summary_text = self._render_summary_text(
            previous_session_id=normalized_session_id,
            status_response=status_response,
            carry_over_items=carry_over_items,
            context_notes=normalized_notes,
        )

        digest = _build_summary_digest(
            previous_session_id=normalized_session_id,
            owner_id=normalized_owner,
            status_response=status_response,
            carry_over_items=carry_over_items,
            context_notes=normalized_notes,
        )

        return SessionCarryOverSummary(
            summary_id=f"carryover-{digest[:20]}",
            previous_session_id=normalized_session_id,
            generated_at=_utc_now_iso(),
            owner_scope=normalized_owner,
            open_loop_count=status_response.metrics.open,
            in_progress_count=status_response.metrics.in_progress,
            blocked_count=status_response.metrics.blocked,
            critical_count=status_response.metrics.critical_open,
            overdue_count=status_response.metrics.overdue_open,
            carry_over_items=carry_over_items,
            context_notes=normalized_notes,
            status_check_summary=status_response.summary,
            summary_text=summary_text,
            deterministic_digest=digest,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _build_items(tasks: tuple[OpenLoopTask, ...], *, limit: int) -> tuple[SessionCarryOverItem, ...]:
        items: list[SessionCarryOverItem] = []
        open_tasks = [task for task in tasks if task.status in {"open", "in_progress", "blocked"}]

        for index, task in enumerate(open_tasks[:limit], start=1):
            items.append(
                SessionCarryOverItem(
                    item_id=f"carry-{index:02d}-{task.task_id}",
                    task_id=task.task_id,
                    title=task.title,
                    priority=task.priority,
                    status=task.status,
                    owner_id=task.owner_id,
                    due_at=task.due_at,
                    metadata=dict(task.metadata),
                )
            )

        return tuple(items)

    @staticmethod
    def _render_summary_text(
        *,
        previous_session_id: str,
        status_response: StatusCheckResponse,
        carry_over_items: tuple[SessionCarryOverItem, ...],
        context_notes: tuple[str, ...],
    ) -> str:
        lines = [
            f"Carry-over summary from {previous_session_id}.",
            (
                "Open loops: "
                f"{status_response.metrics.open + status_response.metrics.in_progress + status_response.metrics.blocked} "
                f"(open={status_response.metrics.open}, in_progress={status_response.metrics.in_progress}, "
                f"blocked={status_response.metrics.blocked})."
            ),
            (
                "Risk markers: "
                f"critical_open={status_response.metrics.critical_open}, "
                f"overdue_open={status_response.metrics.overdue_open}."
            ),
        ]

        if carry_over_items:
            lines.append("Priority carry-over items:")
            for index, item in enumerate(carry_over_items, start=1):
                suffix = f", due={item.due_at}" if item.due_at else ""
                lines.append(
                    f"{index}. [{item.priority}/{item.status}] {item.title} (owner={item.owner_id}{suffix})"
                )
        else:
            lines.append("No active carry-over items.")

        if context_notes:
            lines.append("Context notes:")
            for index, note in enumerate(context_notes, start=1):
                lines.append(f"{index}. {note}")

        return "\n".join(lines)


def _normalize_notes(
    notes: list[str] | tuple[str, ...] | None,
) -> tuple[str, ...]:
    if notes is None:
        return ()
    if not isinstance(notes, (list, tuple)):
        raise TypeError("context_notes must be list or tuple of strings")

    normalized: list[str] = []
    for note in notes:
        value = _normalize_optional(note)
        if value is None:
            continue
        normalized.append(value)

    return tuple(normalized)


def _build_summary_digest(
    *,
    previous_session_id: str,
    owner_id: str | None,
    status_response: StatusCheckResponse,
    carry_over_items: tuple[SessionCarryOverItem, ...],
    context_notes: tuple[str, ...],
) -> str:
    canonical = json.dumps(
        {
            "previous_session_id": previous_session_id,
            "owner_id": owner_id,
            "metrics": {
                "open": status_response.metrics.open,
                "in_progress": status_response.metrics.in_progress,
                "blocked": status_response.metrics.blocked,
                "critical_open": status_response.metrics.critical_open,
                "overdue_open": status_response.metrics.overdue_open,
            },
            "carry_over_items": [
                {
                    "task_id": item.task_id,
                    "priority": item.priority,
                    "status": item.status,
                    "owner_id": item.owner_id,
                    "due_at": item.due_at,
                }
                for item in sorted(carry_over_items, key=lambda entry: entry.item_id)
            ],
            "context_notes": list(context_notes),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise SessionCarryOverError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "SessionCarryOverItem",
    "SessionCarryOverSummary",
    "SessionCarryOverError",
    "PreviousSessionCarryOverWorkflow",
]
