"""Operational event bus with filtered subscriptions for alert-driven workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Any, Literal
from uuid import uuid4

SeverityLevel = Literal["info", "warning", "error", "critical"]

_SEVERITY_RANK: dict[str, int] = {
    "info": 0,
    "warning": 1,
    "error": 2,
    "critical": 3,
}


@dataclass(frozen=True)
class OperationalAlertEvent:
    event_id: str
    event_type: str
    severity: SeverityLevel
    source: str
    message: str
    payload: dict[str, Any]
    occurred_at: str


@dataclass(frozen=True)
class EventSubscription:
    subscriber_id: str
    event_patterns: tuple[str, ...]
    min_severity: SeverityLevel
    source_prefix: str | None
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SubscriptionPollResult:
    subscriber_id: str
    returned: int
    pending_total: int
    events: tuple[OperationalAlertEvent, ...]
    polled_at: str


class EventBusError(ValueError):
    """Raised when event bus operations receive invalid arguments."""


class OperationalEventBus:
    """Stores operational alerts and exposes filtered subscriber views."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, EventSubscription] = {}
        self._events: list[OperationalAlertEvent] = []
        self._acknowledged: dict[str, set[str]] = {}

    def subscribe(
        self,
        *,
        subscriber_id: str,
        event_patterns: list[str] | tuple[str, ...] | None = None,
        min_severity: SeverityLevel | str = "warning",
        source_prefix: str | None = None,
        enabled: bool = True,
    ) -> EventSubscription:
        normalized_id = _normalize_required(subscriber_id, "subscriber_id")
        patterns = tuple(_normalize_patterns(event_patterns or ("*",)))
        normalized_severity = _normalize_severity(min_severity)
        normalized_source_prefix = _normalize_optional(source_prefix)
        now = _utc_now_iso()

        existing = self._subscriptions.get(normalized_id)
        subscription = EventSubscription(
            subscriber_id=normalized_id,
            event_patterns=patterns,
            min_severity=normalized_severity,
            source_prefix=normalized_source_prefix,
            enabled=enabled,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._subscriptions[normalized_id] = subscription
        self._acknowledged.setdefault(normalized_id, set())
        return subscription

    def unsubscribe(self, subscriber_id: str) -> bool:
        normalized_id = _normalize_required(subscriber_id, "subscriber_id")
        if normalized_id not in self._subscriptions:
            return False
        del self._subscriptions[normalized_id]
        self._acknowledged.pop(normalized_id, None)
        return True

    def get_subscription(self, subscriber_id: str) -> EventSubscription:
        normalized_id = _normalize_required(subscriber_id, "subscriber_id")
        subscription = self._subscriptions.get(normalized_id)
        if subscription is None:
            raise KeyError(f"Unknown subscriber: {normalized_id}")
        return subscription

    def list_subscriptions(self, *, enabled: bool | None = None) -> list[EventSubscription]:
        subscriptions = list(self._subscriptions.values())
        if enabled is not None:
            subscriptions = [subscription for subscription in subscriptions if subscription.enabled == enabled]
        subscriptions.sort(key=lambda item: item.subscriber_id)
        return subscriptions

    def publish(
        self,
        *,
        event_type: str,
        severity: SeverityLevel | str,
        source: str,
        message: str,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
        occurred_at: str | None = None,
    ) -> OperationalAlertEvent:
        normalized_type = _normalize_required(event_type, "event_type").lower()
        normalized_severity = _normalize_severity(severity)
        normalized_source = _normalize_required(source, "source")
        normalized_message = _normalize_required(message, "message")
        normalized_event_id = _normalize_required(event_id or str(uuid4()), "event_id")
        normalized_occurred_at = _to_iso(_parse_iso(occurred_at)) if occurred_at is not None else _utc_now_iso()

        event = OperationalAlertEvent(
            event_id=normalized_event_id,
            event_type=normalized_type,
            severity=normalized_severity,
            source=normalized_source,
            message=normalized_message,
            payload=dict(payload or {}),
            occurred_at=normalized_occurred_at,
        )
        self._events.append(event)
        return event

    def poll_subscriber(
        self,
        subscriber_id: str,
        *,
        limit: int = 20,
        include_acknowledged: bool = False,
    ) -> SubscriptionPollResult:
        if limit < 1:
            raise EventBusError("limit must be at least 1")

        subscription = self.get_subscription(subscriber_id)
        if not subscription.enabled:
            return SubscriptionPollResult(
                subscriber_id=subscription.subscriber_id,
                returned=0,
                pending_total=0,
                events=(),
                polled_at=_utc_now_iso(),
            )

        acknowledged = self._acknowledged.setdefault(subscription.subscriber_id, set())
        matched = [event for event in self._events if self._matches(subscription, event)]
        pending = [event for event in matched if event.event_id not in acknowledged]

        visible = matched if include_acknowledged else pending
        visible.sort(key=lambda event: (event.occurred_at, event.event_id), reverse=True)
        limited = tuple(visible[:limit])

        return SubscriptionPollResult(
            subscriber_id=subscription.subscriber_id,
            returned=len(limited),
            pending_total=len(pending),
            events=limited,
            polled_at=_utc_now_iso(),
        )

    def acknowledge(self, subscriber_id: str, event_id: str) -> bool:
        subscription = self.get_subscription(subscriber_id)
        normalized_event_id = _normalize_required(event_id, "event_id")
        if not any(event.event_id == normalized_event_id for event in self._events):
            return False

        matched = any(
            self._matches(subscription, event)
            for event in self._events
            if event.event_id == normalized_event_id
        )
        if not matched:
            return False

        self._acknowledged.setdefault(subscription.subscriber_id, set()).add(normalized_event_id)
        return True

    def _matches(self, subscription: EventSubscription, event: OperationalAlertEvent) -> bool:
        if _SEVERITY_RANK[event.severity] < _SEVERITY_RANK[subscription.min_severity]:
            return False

        if not any(fnmatch(event.event_type, pattern) for pattern in subscription.event_patterns):
            return False

        if subscription.source_prefix is not None:
            if not event.source.startswith(subscription.source_prefix):
                return False

        return True


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise EventBusError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _normalize_patterns(patterns: list[str] | tuple[str, ...]) -> list[str]:
    normalized = sorted({_normalize_required(pattern, "event_pattern").lower() for pattern in patterns})
    if not normalized:
        raise EventBusError("event_patterns must include at least one pattern")
    return normalized


def _normalize_severity(value: SeverityLevel | str) -> SeverityLevel:
    normalized = _normalize_required(str(value), "severity").lower()
    if normalized not in _SEVERITY_RANK:
        allowed = ", ".join(_SEVERITY_RANK.keys())
        raise EventBusError(f"Unsupported severity: {value}. Allowed: {allowed}")
    return normalized  # type: ignore[return-value]


def _parse_iso(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
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


def _utc_now_iso() -> str:
    return _to_iso(datetime.now(timezone.utc))


__all__ = [
    "EventBusError",
    "EventSubscription",
    "OperationalAlertEvent",
    "OperationalEventBus",
    "SeverityLevel",
    "SubscriptionPollResult",
]
