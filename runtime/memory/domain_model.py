"""Memory domain model for short-term, long-term, and preference stores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

NowProvider = Callable[[], datetime]


@dataclass(frozen=True)
class ShortTermMemoryItem:
    memory_id: str
    session_id: str
    key: str
    value: Any
    tags: tuple[str, ...]
    metadata: dict[str, Any]
    version: int
    created_at: str
    updated_at: str
    expires_at: str


@dataclass(frozen=True)
class LongTermMemoryItem:
    memory_id: str
    namespace: str
    key: str
    value: Any
    tags: tuple[str, ...]
    source: str | None
    metadata: dict[str, Any]
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PreferenceMemoryItem:
    preference_id: str
    subject_id: str
    category: str
    key: str
    value: Any
    priority: int
    metadata: dict[str, Any]
    version: int
    created_at: str
    updated_at: str


class MemoryDomainError(ValueError):
    """Raised when memory domain operations violate model constraints."""


class ShortTermMemoryStore:
    """Ephemeral session memory with TTL and per-key version tracking."""

    def __init__(self, *, now_provider: NowProvider | None = None) -> None:
        self._now_provider = now_provider or _utc_now
        self._items: dict[tuple[str, str], ShortTermMemoryItem] = {}

    def put(
        self,
        *,
        session_id: str,
        key: str,
        value: Any,
        ttl_seconds: int = 900,
        tags: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ShortTermMemoryItem:
        if ttl_seconds < 1:
            raise MemoryDomainError("ttl_seconds must be at least 1")

        normalized_session = _normalize_required(session_id, "session_id")
        normalized_key = _normalize_required(key, "key")
        normalized_tags = _normalize_tags(tags)
        now = self._now_provider()
        existing = self._items.get((normalized_session, normalized_key))

        if existing is None:
            item = ShortTermMemoryItem(
                memory_id=str(uuid4()),
                session_id=normalized_session,
                key=normalized_key,
                value=value,
                tags=normalized_tags,
                metadata=dict(metadata or {}),
                version=1,
                created_at=_to_iso(now),
                updated_at=_to_iso(now),
                expires_at=_to_iso(now + timedelta(seconds=ttl_seconds)),
            )
        else:
            item = ShortTermMemoryItem(
                memory_id=existing.memory_id,
                session_id=existing.session_id,
                key=existing.key,
                value=value,
                tags=normalized_tags,
                metadata=dict(metadata or {}),
                version=existing.version + 1,
                created_at=existing.created_at,
                updated_at=_to_iso(now),
                expires_at=_to_iso(now + timedelta(seconds=ttl_seconds)),
            )

        self._items[(normalized_session, normalized_key)] = item
        return item

    def get(self, session_id: str, key: str, *, include_expired: bool = False) -> ShortTermMemoryItem | None:
        normalized_session = _normalize_required(session_id, "session_id")
        normalized_key = _normalize_required(key, "key")
        item = self._items.get((normalized_session, normalized_key))
        if item is None:
            return None

        if not include_expired and self._is_expired(item):
            return None
        return item

    def list_session(self, session_id: str, *, include_expired: bool = False) -> list[ShortTermMemoryItem]:
        normalized_session = _normalize_required(session_id, "session_id")
        items = [item for item in self._items.values() if item.session_id == normalized_session]
        if not include_expired:
            items = [item for item in items if not self._is_expired(item)]
        items.sort(key=lambda item: (item.key, item.updated_at))
        return items

    def purge_expired(self) -> int:
        now = self._now_provider()
        expired_keys = [
            key
            for key, item in self._items.items()
            if _parse_iso(item.expires_at) <= now
        ]
        for key in expired_keys:
            del self._items[key]
        return len(expired_keys)

    def _is_expired(self, item: ShortTermMemoryItem) -> bool:
        return _parse_iso(item.expires_at) <= self._now_provider()


class LongTermMemoryStore:
    """Persistent memory model with namespace-key uniqueness and versioning."""

    def __init__(self, *, now_provider: NowProvider | None = None) -> None:
        self._now_provider = now_provider or _utc_now
        self._items: dict[tuple[str, str], LongTermMemoryItem] = {}

    def upsert(
        self,
        *,
        namespace: str,
        key: str,
        value: Any,
        tags: list[str] | tuple[str, ...] | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LongTermMemoryItem:
        normalized_namespace = _normalize_required(namespace, "namespace")
        normalized_key = _normalize_required(key, "key")
        normalized_source = _normalize_optional(source)
        normalized_tags = _normalize_tags(tags)
        now = self._now_provider()

        existing = self._items.get((normalized_namespace, normalized_key))
        if existing is None:
            item = LongTermMemoryItem(
                memory_id=str(uuid4()),
                namespace=normalized_namespace,
                key=normalized_key,
                value=value,
                tags=normalized_tags,
                source=normalized_source,
                metadata=dict(metadata or {}),
                version=1,
                created_at=_to_iso(now),
                updated_at=_to_iso(now),
            )
        else:
            item = LongTermMemoryItem(
                memory_id=existing.memory_id,
                namespace=existing.namespace,
                key=existing.key,
                value=value,
                tags=normalized_tags,
                source=normalized_source,
                metadata=dict(metadata or {}),
                version=existing.version + 1,
                created_at=existing.created_at,
                updated_at=_to_iso(now),
            )

        self._items[(normalized_namespace, normalized_key)] = item
        return item

    def get(self, namespace: str, key: str) -> LongTermMemoryItem | None:
        normalized_namespace = _normalize_required(namespace, "namespace")
        normalized_key = _normalize_required(key, "key")
        return self._items.get((normalized_namespace, normalized_key))

    def list(self, *, namespace: str | None = None, tag: str | None = None) -> list[LongTermMemoryItem]:
        normalized_namespace = _normalize_optional(namespace)
        normalized_tag = _normalize_optional(tag)

        items = list(self._items.values())
        if normalized_namespace is not None:
            items = [item for item in items if item.namespace == normalized_namespace]
        if normalized_tag is not None:
            normalized_tag = normalized_tag.lower()
            items = [item for item in items if normalized_tag in item.tags]

        items.sort(key=lambda item: (item.namespace, item.key))
        return items

    def delete(self, namespace: str, key: str) -> bool:
        normalized_namespace = _normalize_required(namespace, "namespace")
        normalized_key = _normalize_required(key, "key")
        target = (normalized_namespace, normalized_key)
        if target not in self._items:
            return False
        del self._items[target]
        return True


class PreferenceMemoryStore:
    """Preference memory with subject-specific overrides and fallback resolution."""

    def __init__(self, *, now_provider: NowProvider | None = None) -> None:
        self._now_provider = now_provider or _utc_now
        self._items: dict[tuple[str, str, str], PreferenceMemoryItem] = {}

    def set_preference(
        self,
        *,
        subject_id: str,
        category: str,
        key: str,
        value: Any,
        priority: int = 50,
        metadata: dict[str, Any] | None = None,
    ) -> PreferenceMemoryItem:
        if priority < 0 or priority > 100:
            raise MemoryDomainError("priority must be between 0 and 100")

        normalized_subject = _normalize_required(subject_id, "subject_id")
        normalized_category = _normalize_required(category, "category")
        normalized_key = _normalize_required(key, "key")
        index_key = (normalized_subject, normalized_category, normalized_key)

        now = self._now_provider()
        existing = self._items.get(index_key)
        if existing is None:
            item = PreferenceMemoryItem(
                preference_id=str(uuid4()),
                subject_id=normalized_subject,
                category=normalized_category,
                key=normalized_key,
                value=value,
                priority=priority,
                metadata=dict(metadata or {}),
                version=1,
                created_at=_to_iso(now),
                updated_at=_to_iso(now),
            )
        else:
            item = PreferenceMemoryItem(
                preference_id=existing.preference_id,
                subject_id=existing.subject_id,
                category=existing.category,
                key=existing.key,
                value=value,
                priority=priority,
                metadata=dict(metadata or {}),
                version=existing.version + 1,
                created_at=existing.created_at,
                updated_at=_to_iso(now),
            )

        self._items[index_key] = item
        return item

    def get_preference(self, subject_id: str, category: str, key: str) -> PreferenceMemoryItem | None:
        normalized_subject = _normalize_required(subject_id, "subject_id")
        normalized_category = _normalize_required(category, "category")
        normalized_key = _normalize_required(key, "key")
        return self._items.get((normalized_subject, normalized_category, normalized_key))

    def resolve_preference(
        self,
        *,
        subject_id: str,
        category: str,
        key: str,
        fallback_subjects: list[str] | tuple[str, ...] | None = None,
    ) -> PreferenceMemoryItem | None:
        normalized_subject = _normalize_required(subject_id, "subject_id")
        normalized_category = _normalize_required(category, "category")
        normalized_key = _normalize_required(key, "key")

        subjects = [normalized_subject]
        for fallback in fallback_subjects or ("*",):
            normalized_fallback = _normalize_required(fallback, "fallback_subject")
            if normalized_fallback not in subjects:
                subjects.append(normalized_fallback)

        candidates = [
            self._items[(subject, normalized_category, normalized_key)]
            for subject in subjects
            if (subject, normalized_category, normalized_key) in self._items
        ]
        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (
                0 if item.subject_id == normalized_subject else 1,
                -item.priority,
                -_parse_iso(item.updated_at).timestamp(),
            )
        )
        return candidates[0]

    def list_preferences(
        self,
        *,
        subject_id: str | None = None,
        category: str | None = None,
    ) -> list[PreferenceMemoryItem]:
        normalized_subject = _normalize_optional(subject_id)
        normalized_category = _normalize_optional(category)

        items = list(self._items.values())
        if normalized_subject is not None:
            items = [item for item in items if item.subject_id == normalized_subject]
        if normalized_category is not None:
            items = [item for item in items if item.category == normalized_category]
        items.sort(key=lambda item: (item.subject_id, item.category, item.key))
        return items

    def remove_preference(self, *, subject_id: str, category: str, key: str) -> bool:
        normalized_subject = _normalize_required(subject_id, "subject_id")
        normalized_category = _normalize_required(category, "category")
        normalized_key = _normalize_required(key, "key")
        index_key = (normalized_subject, normalized_category, normalized_key)
        if index_key not in self._items:
            return False
        del self._items[index_key]
        return True


class MemoryDomainModel:
    """Composite access point for short-term, long-term, and preference memory stores."""

    def __init__(self, *, now_provider: NowProvider | None = None) -> None:
        self.short_term = ShortTermMemoryStore(now_provider=now_provider)
        self.long_term = LongTermMemoryStore(now_provider=now_provider)
        self.preferences = PreferenceMemoryStore(now_provider=now_provider)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise MemoryDomainError(f"{field_name} is required")
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


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
