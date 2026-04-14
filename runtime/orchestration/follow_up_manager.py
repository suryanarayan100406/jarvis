"""Follow-up manager for unresolved operational tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

FollowUpStatus = Literal["open", "in_progress", "completed", "cancelled"]
FollowUpPriority = Literal["low", "medium", "high", "critical"]

_PRIORITY_RANK: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


@dataclass(frozen=True)
class FollowUpItem:
    follow_up_id: str
    title: str
    owner_id: str
    status: FollowUpStatus
    priority: FollowUpPriority
    source_type: str
    source_id: str
    due_at: str | None
    metadata: dict[str, Any]
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FollowUpSnapshot:
    total: int
    open: int
    in_progress: int
    completed: int
    cancelled: int
    overdue: int
    generated_at: str


class FollowUpManagerError(ValueError):
    """Raised when follow-up lifecycle operations violate constraints."""


class FollowUpManager:
    """Tracks unresolved work items and supports follow-up closure loops."""

    def __init__(self) -> None:
        self._items: dict[str, FollowUpItem] = {}

    def create_item(
        self,
        *,
        title: str,
        owner_id: str,
        source_type: str,
        source_id: str,
        priority: FollowUpPriority | str = "medium",
        due_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        follow_up_id: str | None = None,
    ) -> FollowUpItem:
        normalized_id = _normalize_required(follow_up_id or str(uuid4()), "follow_up_id")
        if normalized_id in self._items:
            raise FollowUpManagerError(f"Follow-up already exists: {normalized_id}")

        normalized_title = _normalize_required(title, "title")
        normalized_owner = _normalize_required(owner_id, "owner_id")
        normalized_source_type = _normalize_required(source_type, "source_type")
        normalized_source_id = _normalize_required(source_id, "source_id")
        normalized_priority = _normalize_priority(priority)
        normalized_due_at = _normalize_due_at(due_at)
        now = _utc_now_iso()

        item = FollowUpItem(
            follow_up_id=normalized_id,
            title=normalized_title,
            owner_id=normalized_owner,
            status="open",
            priority=normalized_priority,
            source_type=normalized_source_type,
            source_id=normalized_source_id,
            due_at=normalized_due_at,
            metadata=dict(metadata or {}),
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._items[item.follow_up_id] = item
        return item

    def get_item(self, follow_up_id: str) -> FollowUpItem:
        normalized_id = _normalize_required(follow_up_id, "follow_up_id")
        item = self._items.get(normalized_id)
        if item is None:
            raise KeyError(f"Unknown follow-up item: {normalized_id}")
        return item

    def update_status(
        self,
        follow_up_id: str,
        *,
        status: FollowUpStatus | str,
        owner_id: str | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> FollowUpItem:
        item = self.get_item(follow_up_id)
        normalized_status = _normalize_status(status)

        if item.status in {"completed", "cancelled"}:
            raise FollowUpManagerError("Completed or cancelled follow-up items cannot transition")

        normalized_owner = _normalize_required(owner_id, "owner_id") if owner_id is not None else item.owner_id
        updated_metadata = dict(item.metadata)
        if metadata_patch:
            updated_metadata.update(metadata_patch)

        updated = FollowUpItem(
            follow_up_id=item.follow_up_id,
            title=item.title,
            owner_id=normalized_owner,
            status=normalized_status,
            priority=item.priority,
            source_type=item.source_type,
            source_id=item.source_id,
            due_at=item.due_at,
            metadata=updated_metadata,
            version=item.version + 1,
            created_at=item.created_at,
            updated_at=_utc_now_iso(),
        )
        self._items[item.follow_up_id] = updated
        return updated

    def snooze(self, follow_up_id: str, *, due_at: str, reason: str | None = None) -> FollowUpItem:
        item = self.get_item(follow_up_id)
        if item.status in {"completed", "cancelled"}:
            raise FollowUpManagerError("Closed follow-up items cannot be snoozed")

        updated_metadata = dict(item.metadata)
        if reason is not None:
            updated_metadata["snooze_reason"] = _normalize_required(reason, "reason")

        updated = FollowUpItem(
            follow_up_id=item.follow_up_id,
            title=item.title,
            owner_id=item.owner_id,
            status=item.status,
            priority=item.priority,
            source_type=item.source_type,
            source_id=item.source_id,
            due_at=_normalize_due_at(due_at),
            metadata=updated_metadata,
            version=item.version + 1,
            created_at=item.created_at,
            updated_at=_utc_now_iso(),
        )
        self._items[item.follow_up_id] = updated
        return updated

    def list_items(
        self,
        *,
        status: FollowUpStatus | str | None = None,
        owner_id: str | None = None,
        include_closed: bool = False,
    ) -> list[FollowUpItem]:
        normalized_status = _normalize_status(status) if status is not None else None
        normalized_owner = _normalize_optional(owner_id)

        items = list(self._items.values())
        if not include_closed:
            items = [item for item in items if item.status not in {"completed", "cancelled"}]
        if normalized_status is not None:
            items = [item for item in items if item.status == normalized_status]
        if normalized_owner is not None:
            items = [item for item in items if item.owner_id == normalized_owner]

        items.sort(
            key=lambda item: (
                -_PRIORITY_RANK[item.priority],
                item.due_at or "9999-12-31T23:59:59Z",
                item.follow_up_id,
            )
        )
        return items

    def list_overdue(self, *, reference_time: str | None = None) -> list[FollowUpItem]:
        now = _parse_iso(reference_time) if reference_time is not None else _utc_now()
        overdue = [
            item
            for item in self.list_items(include_closed=False)
            if item.due_at is not None and _parse_iso(item.due_at) < now
        ]
        overdue.sort(key=lambda item: (item.due_at or "", -_PRIORITY_RANK[item.priority]))
        return overdue

    def snapshot(self, *, reference_time: str | None = None) -> FollowUpSnapshot:
        now = _parse_iso(reference_time) if reference_time is not None else _utc_now()
        items = list(self._items.values())
        overdue_count = sum(
            1
            for item in items
            if item.status in {"open", "in_progress"}
            and item.due_at is not None
            and _parse_iso(item.due_at) < now
        )
        return FollowUpSnapshot(
            total=len(items),
            open=sum(1 for item in items if item.status == "open"),
            in_progress=sum(1 for item in items if item.status == "in_progress"),
            completed=sum(1 for item in items if item.status == "completed"),
            cancelled=sum(1 for item in items if item.status == "cancelled"),
            overdue=overdue_count,
            generated_at=_to_iso(now),
        )


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise FollowUpManagerError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _normalize_status(value: FollowUpStatus | str) -> FollowUpStatus:
    normalized = _normalize_required(str(value), "status").lower()
    if normalized not in {"open", "in_progress", "completed", "cancelled"}:
        raise FollowUpManagerError(f"Unsupported status: {value}")
    return normalized  # type: ignore[return-value]


def _normalize_priority(value: FollowUpPriority | str) -> FollowUpPriority:
    normalized = _normalize_required(str(value), "priority").lower()
    if normalized not in _PRIORITY_RANK:
        raise FollowUpManagerError(f"Unsupported priority: {value}")
    return normalized  # type: ignore[return-value]


def _normalize_due_at(value: str | None) -> str | None:
    if value is None:
        return None
    return _to_iso(_parse_iso(value))


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
    "FollowUpItem",
    "FollowUpManager",
    "FollowUpManagerError",
    "FollowUpPriority",
    "FollowUpSnapshot",
    "FollowUpStatus",
]
