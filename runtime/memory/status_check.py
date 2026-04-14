"""Status check command and summary renderer for memory open-loop tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .open_loop_register import OpenLoopTask

_OPEN_STATUSES: set[str] = {"open", "in_progress", "blocked"}


@dataclass(frozen=True)
class StatusCheckMetrics:
    total: int
    open: int
    in_progress: int
    blocked: int
    completed: int
    cancelled: int
    critical_open: int
    overdue_open: int
    generated_at: str


@dataclass(frozen=True)
class StatusCheckResponse:
    actor_id: str
    scope_owner_id: str | None
    include_completed: bool
    task_limit: int
    tasks: tuple[OpenLoopTask, ...]
    metrics: StatusCheckMetrics
    summary: str


class StatusCheckCommandError(ValueError):
    """Raised when status check command parameters violate constraints."""


class StatusSummaryRenderer:
    """Renders concise open-loop status summaries for operator status checks."""

    def render(
        self,
        *,
        metrics: StatusCheckMetrics,
        tasks: tuple[OpenLoopTask, ...],
        limit: int,
        owner_id: str | None = None,
        include_completed: bool = False,
    ) -> str:
        if limit < 1:
            raise StatusCheckCommandError("limit must be at least 1")

        scope_label = f"owner={owner_id}" if owner_id else "global"
        active_total = metrics.open + metrics.in_progress + metrics.blocked

        if metrics.total == 0:
            return f"Status check ({scope_label}): no tracked tasks."

        if active_total == 0:
            closed_total = metrics.completed + metrics.cancelled
            return (
                f"Status check ({scope_label}): no open loops. "
                f"Closed tasks: {closed_total}."
            )

        header = (
            f"Status check ({scope_label}): {active_total} open loops "
            f"(open {metrics.open}, in_progress {metrics.in_progress}, blocked {metrics.blocked})."
        )
        extras: list[str] = []
        if metrics.critical_open:
            extras.append(f"Critical open: {metrics.critical_open}")
        if metrics.overdue_open:
            extras.append(f"Overdue open: {metrics.overdue_open}")
        if include_completed and (metrics.completed or metrics.cancelled):
            extras.append(f"Closed in scope: {metrics.completed + metrics.cancelled}")

        active_tasks = [task for task in tasks if task.status in _OPEN_STATUSES]
        top_lines: list[str] = []
        for index, task in enumerate(active_tasks[:limit], start=1):
            detail_parts: list[str] = [f"owner={task.owner_id}"]
            if task.due_at is not None:
                detail_parts.append(f"due={task.due_at}")
            top_lines.append(
                f"{index}. [{task.priority}/{task.status}] {task.title} "
                f"({', '.join(detail_parts)})"
            )

        summary_lines = [header]
        if extras:
            summary_lines.append("; ".join(extras) + ".")
        summary_lines.append("Top open loops:")
        if top_lines:
            summary_lines.extend(top_lines)
        else:
            summary_lines.append("1. No active tasks in scope.")
        return "\n".join(summary_lines)


class StatusCheckCommand:
    """Builds status-check responses from tracked open-loop task state."""

    def __init__(self, register: object, renderer: StatusSummaryRenderer | None = None) -> None:
        self._register = register
        self._renderer = renderer or StatusSummaryRenderer()

    def execute(
        self,
        *,
        actor_id: str = "boss",
        owner_id: str | None = None,
        include_completed: bool = False,
        limit: int = 5,
        reference_time: str | None = None,
    ) -> StatusCheckResponse:
        normalized_actor = _normalize_required(actor_id, "actor_id")
        normalized_owner = _normalize_optional(owner_id)
        if limit < 1:
            raise StatusCheckCommandError("limit must be at least 1")

        scoped = self._register.list_tasks(owner_id=normalized_owner, include_closed=True)
        if include_completed:
            visible_tasks = tuple(scoped)
        else:
            visible_tasks = tuple(task for task in scoped if task.status in _OPEN_STATUSES)

        metrics = _build_metrics(visible_tasks, reference_time=reference_time)
        summary = self._renderer.render(
            metrics=metrics,
            tasks=visible_tasks,
            limit=limit,
            owner_id=normalized_owner,
            include_completed=include_completed,
        )

        return StatusCheckResponse(
            actor_id=normalized_actor,
            scope_owner_id=normalized_owner,
            include_completed=include_completed,
            task_limit=limit,
            tasks=visible_tasks,
            metrics=metrics,
            summary=summary,
        )


def _build_metrics(tasks: tuple[OpenLoopTask, ...], *, reference_time: str | None) -> StatusCheckMetrics:
    now = _parse_iso(reference_time) if reference_time is not None else _utc_now()
    open_count = sum(1 for task in tasks if task.status == "open")
    in_progress_count = sum(1 for task in tasks if task.status == "in_progress")
    blocked_count = sum(1 for task in tasks if task.status == "blocked")
    completed_count = sum(1 for task in tasks if task.status == "completed")
    cancelled_count = sum(1 for task in tasks if task.status == "cancelled")

    critical_open = sum(
        1 for task in tasks if task.status in _OPEN_STATUSES and task.priority == "critical"
    )
    overdue_open = sum(
        1
        for task in tasks
        if task.status in _OPEN_STATUSES and task.due_at is not None and _parse_iso(task.due_at) < now
    )

    return StatusCheckMetrics(
        total=len(tasks),
        open=open_count,
        in_progress=in_progress_count,
        blocked=blocked_count,
        completed=completed_count,
        cancelled=cancelled_count,
        critical_open=critical_open,
        overdue_open=overdue_open,
        generated_at=_to_iso(now),
    )


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise StatusCheckCommandError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


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


__all__ = [
    "StatusCheckCommand",
    "StatusCheckCommandError",
    "StatusCheckMetrics",
    "StatusCheckResponse",
    "StatusSummaryRenderer",
]
