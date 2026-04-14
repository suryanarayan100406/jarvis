"""Scheduler for cron-style and calendar-based autonomous triggers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

ScheduleKind = Literal["cron", "calendar"]

_MONTH_ALIASES: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_WEEKDAY_ALIASES: dict[str, int] = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


@dataclass(frozen=True)
class SchedulerTrigger:
    trigger_id: str
    name: str
    kind: ScheduleKind
    cron_expression: str | None
    calendar_times: tuple[str, ...]
    payload: dict[str, Any]
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TriggerActivation:
    trigger_id: str
    name: str
    kind: ScheduleKind
    scheduled_for: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SchedulerPollResult:
    polled_at: str
    due_count: int
    activations: tuple[TriggerActivation, ...]


class SchedulerError(ValueError):
    """Raised when trigger registration or scheduler operations are invalid."""


class AutonomousScheduler:
    """Evaluates cron and calendar triggers and emits due activations once."""

    def __init__(self) -> None:
        self._triggers: dict[str, SchedulerTrigger] = {}
        self._fired_schedule_keys: dict[str, set[str]] = {}

    def register_cron_trigger(
        self,
        *,
        trigger_id: str,
        name: str,
        expression: str,
        payload: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> SchedulerTrigger:
        normalized_id = _normalize_required(trigger_id, "trigger_id")
        if normalized_id in self._triggers:
            raise SchedulerError(f"Trigger already exists: {normalized_id}")

        normalized_name = _normalize_required(name, "name")
        normalized_expression = _normalize_required(expression, "expression")
        _parse_cron(normalized_expression)

        now = _utc_now_iso()
        trigger = SchedulerTrigger(
            trigger_id=normalized_id,
            name=normalized_name,
            kind="cron",
            cron_expression=normalized_expression,
            calendar_times=(),
            payload=dict(payload or {}),
            enabled=enabled,
            created_at=now,
            updated_at=now,
        )
        self._triggers[normalized_id] = trigger
        self._fired_schedule_keys[normalized_id] = set()
        return trigger

    def register_calendar_trigger(
        self,
        *,
        trigger_id: str,
        name: str,
        run_at: list[str] | tuple[str, ...],
        payload: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> SchedulerTrigger:
        normalized_id = _normalize_required(trigger_id, "trigger_id")
        if normalized_id in self._triggers:
            raise SchedulerError(f"Trigger already exists: {normalized_id}")

        normalized_name = _normalize_required(name, "name")
        if not run_at:
            raise SchedulerError("run_at must include at least one datetime")

        normalized_times = sorted({_to_iso(_parse_iso(value)) for value in run_at})
        now = _utc_now_iso()

        trigger = SchedulerTrigger(
            trigger_id=normalized_id,
            name=normalized_name,
            kind="calendar",
            cron_expression=None,
            calendar_times=tuple(normalized_times),
            payload=dict(payload or {}),
            enabled=enabled,
            created_at=now,
            updated_at=now,
        )
        self._triggers[normalized_id] = trigger
        self._fired_schedule_keys[normalized_id] = set()
        return trigger

    def get_trigger(self, trigger_id: str) -> SchedulerTrigger:
        normalized_id = _normalize_required(trigger_id, "trigger_id")
        trigger = self._triggers.get(normalized_id)
        if trigger is None:
            raise KeyError(f"Unknown trigger: {normalized_id}")
        return trigger

    def list_triggers(
        self,
        *,
        kind: ScheduleKind | None = None,
        enabled: bool | None = None,
    ) -> list[SchedulerTrigger]:
        triggers = list(self._triggers.values())
        if kind is not None:
            triggers = [trigger for trigger in triggers if trigger.kind == kind]
        if enabled is not None:
            triggers = [trigger for trigger in triggers if trigger.enabled == enabled]
        triggers.sort(key=lambda trigger: trigger.trigger_id)
        return triggers

    def set_trigger_enabled(self, trigger_id: str, enabled: bool) -> SchedulerTrigger:
        trigger = self.get_trigger(trigger_id)
        updated = SchedulerTrigger(
            trigger_id=trigger.trigger_id,
            name=trigger.name,
            kind=trigger.kind,
            cron_expression=trigger.cron_expression,
            calendar_times=trigger.calendar_times,
            payload=dict(trigger.payload),
            enabled=enabled,
            created_at=trigger.created_at,
            updated_at=_utc_now_iso(),
        )
        self._triggers[trigger.trigger_id] = updated
        return updated

    def poll_due(self, *, reference_time: str | None = None) -> SchedulerPollResult:
        now = _parse_iso(reference_time) if reference_time is not None else _utc_now()
        current_minute = _floor_to_minute(now)
        current_minute_iso = _to_iso(current_minute)

        activations: list[TriggerActivation] = []
        for trigger in self.list_triggers(enabled=True):
            if trigger.kind == "cron":
                activation = self._poll_cron(trigger, current_minute, current_minute_iso)
                if activation is not None:
                    activations.append(activation)
                continue

            activations.extend(self._poll_calendar(trigger, now))

        activations.sort(key=lambda item: (item.scheduled_for, item.trigger_id))
        return SchedulerPollResult(
            polled_at=_to_iso(now),
            due_count=len(activations),
            activations=tuple(activations),
        )

    def next_run_at(self, trigger_id: str, *, reference_time: str | None = None) -> str | None:
        trigger = self.get_trigger(trigger_id)
        now = _parse_iso(reference_time) if reference_time is not None else _utc_now()

        if trigger.kind == "calendar":
            fired = self._fired_schedule_keys.get(trigger.trigger_id, set())
            for scheduled_for in trigger.calendar_times:
                if scheduled_for in fired:
                    continue
                if _parse_iso(scheduled_for) >= now:
                    return scheduled_for
            return None

        expression = trigger.cron_expression
        if expression is None:
            return None
        parser = _parse_cron(expression)
        probe = _floor_to_minute(now)
        for _ in range(0, 525600):
            probe = probe + timedelta(minutes=1)
            if _cron_matches(parser, probe):
                return _to_iso(probe)
        return None

    def _poll_cron(
        self,
        trigger: SchedulerTrigger,
        current_minute: datetime,
        current_minute_iso: str,
    ) -> TriggerActivation | None:
        expression = trigger.cron_expression
        if expression is None:
            return None
        parser = _parse_cron(expression)
        if not _cron_matches(parser, current_minute):
            return None

        fired_keys = self._fired_schedule_keys.setdefault(trigger.trigger_id, set())
        if current_minute_iso in fired_keys:
            return None
        fired_keys.add(current_minute_iso)

        return TriggerActivation(
            trigger_id=trigger.trigger_id,
            name=trigger.name,
            kind=trigger.kind,
            scheduled_for=current_minute_iso,
            payload=dict(trigger.payload),
        )

    def _poll_calendar(self, trigger: SchedulerTrigger, now: datetime) -> list[TriggerActivation]:
        fired_keys = self._fired_schedule_keys.setdefault(trigger.trigger_id, set())
        activations: list[TriggerActivation] = []

        for scheduled_for in trigger.calendar_times:
            if scheduled_for in fired_keys:
                continue
            if _parse_iso(scheduled_for) > now:
                continue
            fired_keys.add(scheduled_for)
            activations.append(
                TriggerActivation(
                    trigger_id=trigger.trigger_id,
                    name=trigger.name,
                    kind=trigger.kind,
                    scheduled_for=scheduled_for,
                    payload=dict(trigger.payload),
                )
            )

        return activations


@dataclass(frozen=True)
class _CronParser:
    minute: frozenset[int]
    hour: frozenset[int]
    day: frozenset[int]
    month: frozenset[int]
    weekday: frozenset[int]


def _parse_cron(expression: str) -> _CronParser:
    parts = expression.split()
    if len(parts) != 5:
        raise SchedulerError("Cron expression must contain exactly 5 fields")

    minute = frozenset(_expand_field(parts[0], minimum=0, maximum=59))
    hour = frozenset(_expand_field(parts[1], minimum=0, maximum=23))
    day = frozenset(_expand_field(parts[2], minimum=1, maximum=31))
    month = frozenset(_expand_field(parts[3], minimum=1, maximum=12, aliases=_MONTH_ALIASES))
    weekday = frozenset(_expand_field(parts[4], minimum=0, maximum=7, aliases=_WEEKDAY_ALIASES))

    normalized_weekday = {0 if value == 7 else value for value in weekday}
    return _CronParser(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        weekday=frozenset(normalized_weekday),
    )


def _cron_matches(parser: _CronParser, when: datetime) -> bool:
    cron_weekday = (when.weekday() + 1) % 7
    return (
        when.minute in parser.minute
        and when.hour in parser.hour
        and when.day in parser.day
        and when.month in parser.month
        and cron_weekday in parser.weekday
    )


def _expand_field(
    field: str,
    *,
    minimum: int,
    maximum: int,
    aliases: dict[str, int] | None = None,
) -> set[int]:
    normalized_field = _normalize_required(field, "cron_field").lower()
    values: set[int] = set()

    for chunk in normalized_field.split(","):
        part = chunk.strip()
        if not part:
            raise SchedulerError("Cron field contains an empty segment")

        if "/" in part:
            base, step_text = part.split("/", 1)
            step = _parse_int(step_text, field_name="cron_step")
            if step < 1:
                raise SchedulerError("Cron step must be at least 1")
            range_values = _expand_base(base, minimum=minimum, maximum=maximum, aliases=aliases)
            ordered = sorted(range_values)
            if not ordered:
                continue
            start = ordered[0]
            for value in ordered:
                if (value - start) % step == 0:
                    values.add(value)
            continue

        values.update(_expand_base(part, minimum=minimum, maximum=maximum, aliases=aliases))

    if not values:
        raise SchedulerError("Cron field resolved to no values")
    return values


def _expand_base(
    base: str,
    *,
    minimum: int,
    maximum: int,
    aliases: dict[str, int] | None,
) -> set[int]:
    if base == "*":
        return set(range(minimum, maximum + 1))

    if "-" in base:
        left, right = base.split("-", 1)
        start = _resolve_value(left, minimum=minimum, maximum=maximum, aliases=aliases)
        end = _resolve_value(right, minimum=minimum, maximum=maximum, aliases=aliases)
        if end < start:
            raise SchedulerError("Cron range end must be greater than or equal to start")
        return set(range(start, end + 1))

    value = _resolve_value(base, minimum=minimum, maximum=maximum, aliases=aliases)
    return {value}


def _resolve_value(
    token: str,
    *,
    minimum: int,
    maximum: int,
    aliases: dict[str, int] | None,
) -> int:
    normalized = token.strip().lower()
    if aliases and normalized in aliases:
        value = aliases[normalized]
    else:
        value = _parse_int(normalized, field_name="cron_value")

    if value < minimum or value > maximum:
        raise SchedulerError(f"Cron value out of range: {value}. Allowed: {minimum}-{maximum}")
    return value


def _parse_int(value: str, *, field_name: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise SchedulerError(f"{field_name} must be an integer: {value}") from exc


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise SchedulerError(f"{field_name} is required")
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


def _floor_to_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _to_iso(_utc_now())


__all__ = [
    "AutonomousScheduler",
    "ScheduleKind",
    "SchedulerError",
    "SchedulerPollResult",
    "SchedulerTrigger",
    "TriggerActivation",
]
