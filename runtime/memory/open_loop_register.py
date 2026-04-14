"""Open-loop task register service for session lifecycle tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

TaskStatus = Literal["open", "in_progress", "blocked", "completed", "cancelled"]
TaskPriority = Literal["low", "medium", "high", "critical"]

_STATUS_VALUES: set[str] = {"open", "in_progress", "blocked", "completed", "cancelled"}
_PRIORITY_VALUES: set[str] = {"low", "medium", "high", "critical"}
_PRIORITY_RANK: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


@dataclass(frozen=True)
class OpenLoopTask:
    task_id: str
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    owner_id: str
    due_at: str | None
    tags: tuple[str, ...]
    metadata: dict[str, Any]
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class OpenLoopTaskEvent:
    task_id: str
    event_type: str
    version: int
    changed_fields: tuple[str, ...]
    note: str | None
    created_at: str


@dataclass(frozen=True)
class OpenLoopRegisterSnapshot:
    total: int
    open: int
    in_progress: int
    blocked: int
    completed: int
    cancelled: int
    critical_open: int
    overdue_open: int
    generated_at: str


class OpenLoopRegisterError(ValueError):
    """Raised when open-loop register operations violate task constraints."""


class OpenLoopTaskRegister:
    """Maintains versioned task state for pending and active open loops."""

    def __init__(self) -> None:
        self._tasks: dict[str, OpenLoopTask] = {}
        self._events: dict[str, list[OpenLoopTaskEvent]] = {}

    def create_task(
        self,
        *,
        title: str,
        description: str | None = None,
        priority: TaskPriority | str = "medium",
        owner_id: str = "system",
        due_at: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> OpenLoopTask:
        normalized_title = _normalize_required(title, "title")
        normalized_description = _normalize_optional(description)
        normalized_priority = self._normalize_priority(priority)
        normalized_owner = _normalize_required(owner_id, "owner_id")
        normalized_due_at = _normalize_due_at(due_at)
        normalized_tags = _normalize_tags(tags)
        now = _utc_now_iso()

        assigned_task_id = _normalize_required(task_id or str(uuid4()), "task_id")
        if assigned_task_id in self._tasks:
            raise OpenLoopRegisterError(f"Task already exists: {assigned_task_id}")

        task = OpenLoopTask(
            task_id=assigned_task_id,
            title=normalized_title,
            description=normalized_description,
            status="open",
            priority=normalized_priority,
            owner_id=normalized_owner,
            due_at=normalized_due_at,
            tags=normalized_tags,
            metadata=dict(metadata or {}),
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._tasks[assigned_task_id] = task
        self._record_event(task, event_type="created", changed_fields=("*",), note=None)
        return task

    def get_task(self, task_id: str) -> OpenLoopTask:
        normalized_task_id = _normalize_required(task_id, "task_id")
        task = self._tasks.get(normalized_task_id)
        if task is None:
            raise KeyError(f"Unknown task: {normalized_task_id}")
        return task

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        status: TaskStatus | str | None = None,
        priority: TaskPriority | str | None = None,
        owner_id: str | None = None,
        due_at: str | None = None,
        clear_due_at: bool = False,
        tags: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> OpenLoopTask:
        task = self.get_task(task_id)
        changed_fields: list[str] = []

        updated_title = task.title
        if title is not None:
            updated_title = _normalize_required(title, "title")
            if updated_title != task.title:
                changed_fields.append("title")

        updated_description = task.description
        if description is not None:
            updated_description = _normalize_optional(description)
            if updated_description != task.description:
                changed_fields.append("description")

        updated_status = task.status
        if status is not None:
            updated_status = self._normalize_status(status)
            if updated_status != task.status:
                changed_fields.append("status")

        updated_priority = task.priority
        if priority is not None:
            updated_priority = self._normalize_priority(priority)
            if updated_priority != task.priority:
                changed_fields.append("priority")

        updated_owner = task.owner_id
        if owner_id is not None:
            updated_owner = _normalize_required(owner_id, "owner_id")
            if updated_owner != task.owner_id:
                changed_fields.append("owner_id")

        updated_due_at = task.due_at
        if clear_due_at:
            updated_due_at = None
            if task.due_at is not None:
                changed_fields.append("due_at")
        elif due_at is not None:
            updated_due_at = _normalize_due_at(due_at)
            if updated_due_at != task.due_at:
                changed_fields.append("due_at")

        updated_tags = task.tags
        if tags is not None:
            updated_tags = _normalize_tags(tags)
            if updated_tags != task.tags:
                changed_fields.append("tags")

        updated_metadata = task.metadata
        if metadata is not None:
            updated_metadata = dict(metadata)
            if updated_metadata != task.metadata:
                changed_fields.append("metadata")

        if not changed_fields and note is None:
            return task

        updated_task = OpenLoopTask(
            task_id=task.task_id,
            title=updated_title,
            description=updated_description,
            status=updated_status,
            priority=updated_priority,
            owner_id=updated_owner,
            due_at=updated_due_at,
            tags=updated_tags,
            metadata=updated_metadata,
            version=task.version + 1,
            created_at=task.created_at,
            updated_at=_utc_now_iso(),
        )
        self._tasks[task.task_id] = updated_task
        self._record_event(
            updated_task,
            event_type="updated",
            changed_fields=tuple(sorted(set(changed_fields))),
            note=_normalize_optional(note),
        )
        return updated_task

    def list_tasks(
        self,
        *,
        status: TaskStatus | str | None = None,
        owner_id: str | None = None,
        priority: TaskPriority | str | None = None,
        include_closed: bool = False,
    ) -> list[OpenLoopTask]:
        normalized_status = self._normalize_status(status) if status is not None else None
        normalized_owner = _normalize_optional(owner_id)
        normalized_priority = self._normalize_priority(priority) if priority is not None else None

        tasks = list(self._tasks.values())
        if not include_closed:
            tasks = [task for task in tasks if task.status not in {"completed", "cancelled"}]
        if normalized_status is not None:
            tasks = [task for task in tasks if task.status == normalized_status]
        if normalized_owner is not None:
            tasks = [task for task in tasks if task.owner_id == normalized_owner]
        if normalized_priority is not None:
            tasks = [task for task in tasks if task.priority == normalized_priority]

        tasks.sort(
            key=lambda task: (
                -_PRIORITY_RANK[task.priority],
                task.status,
                task.updated_at,
                task.task_id,
            )
        )
        return tasks

    def get_events(self, task_id: str) -> list[OpenLoopTaskEvent]:
        normalized_task_id = _normalize_required(task_id, "task_id")
        if normalized_task_id not in self._tasks and normalized_task_id not in self._events:
            raise KeyError(f"Unknown task: {normalized_task_id}")
        return list(self._events.get(normalized_task_id, []))

    def remove_task(self, task_id: str, *, note: str | None = None) -> bool:
        normalized_task_id = _normalize_required(task_id, "task_id")
        task = self._tasks.get(normalized_task_id)
        if task is None:
            return False

        removed_task = OpenLoopTask(
            task_id=task.task_id,
            title=task.title,
            description=task.description,
            status="cancelled",
            priority=task.priority,
            owner_id=task.owner_id,
            due_at=task.due_at,
            tags=task.tags,
            metadata=task.metadata,
            version=task.version + 1,
            created_at=task.created_at,
            updated_at=_utc_now_iso(),
        )
        self._tasks[normalized_task_id] = removed_task
        self._record_event(
            removed_task,
            event_type="removed",
            changed_fields=("status",),
            note=_normalize_optional(note),
        )
        return True

    def snapshot(self, *, reference_time: str | None = None) -> OpenLoopRegisterSnapshot:
        now = _parse_iso(reference_time) if reference_time is not None else _utc_now()
        tasks = list(self._tasks.values())

        open_count = sum(1 for task in tasks if task.status == "open")
        in_progress_count = sum(1 for task in tasks if task.status == "in_progress")
        blocked_count = sum(1 for task in tasks if task.status == "blocked")
        completed_count = sum(1 for task in tasks if task.status == "completed")
        cancelled_count = sum(1 for task in tasks if task.status == "cancelled")

        critical_open = sum(
            1
            for task in tasks
            if task.status in {"open", "in_progress", "blocked"} and task.priority == "critical"
        )
        overdue_open = sum(
            1
            for task in tasks
            if task.status in {"open", "in_progress", "blocked"}
            and task.due_at is not None
            and _parse_iso(task.due_at) < now
        )

        return OpenLoopRegisterSnapshot(
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

    @staticmethod
    def _normalize_status(value: TaskStatus | str) -> TaskStatus:
        normalized = _normalize_required(str(value), "status").lower()
        if normalized not in _STATUS_VALUES:
            allowed = ", ".join(sorted(_STATUS_VALUES))
            raise OpenLoopRegisterError(f"Unsupported task status: {value}. Allowed: {allowed}")
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _normalize_priority(value: TaskPriority | str) -> TaskPriority:
        normalized = _normalize_required(str(value), "priority").lower()
        if normalized not in _PRIORITY_VALUES:
            allowed = ", ".join(sorted(_PRIORITY_VALUES))
            raise OpenLoopRegisterError(f"Unsupported task priority: {value}. Allowed: {allowed}")
        return normalized  # type: ignore[return-value]

    def _record_event(
        self,
        task: OpenLoopTask,
        *,
        event_type: str,
        changed_fields: tuple[str, ...],
        note: str | None,
    ) -> None:
        event = OpenLoopTaskEvent(
            task_id=task.task_id,
            event_type=event_type,
            version=task.version,
            changed_fields=changed_fields,
            note=note,
            created_at=_utc_now_iso(),
        )
        self._events.setdefault(task.task_id, []).append(event)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise OpenLoopRegisterError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _normalize_tags(tags: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if tags is None:
        return ()
    normalized = sorted({_normalize_required(tag, "tag").lower() for tag in tags})
    return tuple(normalized)


def _normalize_due_at(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = _parse_iso(value)
    return _to_iso(parsed)


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
