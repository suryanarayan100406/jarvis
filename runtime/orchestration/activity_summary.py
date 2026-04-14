"""Daily and weekly summary generation for autonomous operations activity."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

SummaryPeriod = Literal["daily", "weekly"]


@dataclass(frozen=True)
class AutonomousActivityRecord:
    activity_id: str
    category: str
    status: str
    summary: str
    severity: str
    occurred_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AutonomousActivitySummary:
    period: SummaryPeriod
    window_start: str
    window_end: str
    total_activities: int
    success_count: int
    degraded_count: int
    failed_count: int
    critical_alerts: int
    top_categories: tuple[str, ...]
    open_follow_ups: int
    markdown: str
    generated_at: str


class ActivitySummaryError(ValueError):
    """Raised when activity summary operations receive invalid input."""


class AutonomousActivitySummaryGenerator:
    """Stores activity records and renders daily or weekly autonomous summaries."""

    def __init__(self) -> None:
        self._records: list[AutonomousActivityRecord] = []

    def record_activity(
        self,
        *,
        category: str,
        status: str,
        summary: str,
        severity: str = "info",
        occurred_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        activity_id: str | None = None,
    ) -> AutonomousActivityRecord:
        normalized_category = _normalize_required(category, "category").lower()
        normalized_status = _normalize_required(status, "status").lower()
        normalized_summary = _normalize_required(summary, "summary")
        normalized_severity = _normalize_required(severity, "severity").lower()
        if normalized_severity not in {"info", "warning", "error", "critical"}:
            raise ActivitySummaryError("severity must be one of info, warning, error, critical")

        occurred = _parse_iso(occurred_at) if occurred_at is not None else _utc_now()
        record = AutonomousActivityRecord(
            activity_id=_normalize_required(activity_id or str(uuid4()), "activity_id"),
            category=normalized_category,
            status=normalized_status,
            summary=normalized_summary,
            severity=normalized_severity,
            occurred_at=_to_iso(occurred),
            metadata=dict(metadata or {}),
        )
        self._records.append(record)
        return record

    def list_activities(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        category: str | None = None,
    ) -> list[AutonomousActivityRecord]:
        start = _parse_iso(start_time) if start_time is not None else None
        end = _parse_iso(end_time) if end_time is not None else None
        normalized_category = _normalize_optional(category)

        filtered = []
        for record in self._records:
            occurred = _parse_iso(record.occurred_at)
            if start is not None and occurred < start:
                continue
            if end is not None and occurred > end:
                continue
            if normalized_category is not None and record.category != normalized_category:
                continue
            filtered.append(record)

        filtered.sort(key=lambda record: record.occurred_at)
        return filtered

    def generate_summary(
        self,
        *,
        period: SummaryPeriod,
        reference_time: str | None = None,
        open_follow_ups: int = 0,
    ) -> AutonomousActivitySummary:
        if period not in {"daily", "weekly"}:
            raise ActivitySummaryError("period must be daily or weekly")
        if open_follow_ups < 0:
            raise ActivitySummaryError("open_follow_ups must be non-negative")

        now = _parse_iso(reference_time) if reference_time is not None else _utc_now()
        window_start = now - (timedelta(days=1) if period == "daily" else timedelta(days=7))
        records = self.list_activities(start_time=_to_iso(window_start), end_time=_to_iso(now))

        total = len(records)
        success_count = sum(1 for record in records if record.status == "success")
        degraded_count = sum(1 for record in records if record.status == "degraded")
        failed_count = sum(1 for record in records if record.status in {"failed", "timeout", "error"})
        critical_alerts = sum(1 for record in records if record.severity == "critical")

        category_counts = Counter(record.category for record in records)
        top_categories = tuple(category for category, _ in category_counts.most_common(3))
        markdown = self._render_markdown(
            period=period,
            window_start=window_start,
            window_end=now,
            total=total,
            success_count=success_count,
            degraded_count=degraded_count,
            failed_count=failed_count,
            critical_alerts=critical_alerts,
            top_categories=top_categories,
            open_follow_ups=open_follow_ups,
            records=records,
        )

        return AutonomousActivitySummary(
            period=period,
            window_start=_to_iso(window_start),
            window_end=_to_iso(now),
            total_activities=total,
            success_count=success_count,
            degraded_count=degraded_count,
            failed_count=failed_count,
            critical_alerts=critical_alerts,
            top_categories=top_categories,
            open_follow_ups=open_follow_ups,
            markdown=markdown,
            generated_at=_utc_now_iso(),
        )

    @staticmethod
    def _render_markdown(
        *,
        period: SummaryPeriod,
        window_start: datetime,
        window_end: datetime,
        total: int,
        success_count: int,
        degraded_count: int,
        failed_count: int,
        critical_alerts: int,
        top_categories: tuple[str, ...],
        open_follow_ups: int,
        records: list[AutonomousActivityRecord],
    ) -> str:
        lines = [
            f"# Autonomous Activity Summary ({period})",
            "",
            f"Window: {_to_iso(window_start)} to {_to_iso(window_end)}",
            f"Total activities: {total}",
            f"Success: {success_count}",
            f"Degraded: {degraded_count}",
            f"Failed: {failed_count}",
            f"Critical alerts: {critical_alerts}",
            f"Open follow-ups: {open_follow_ups}",
            "",
            "## Top Categories",
        ]

        if top_categories:
            lines.extend([f"- {category}" for category in top_categories])
        else:
            lines.append("- none")

        lines.append("")
        lines.append("## Recent Highlights")
        recent = list(reversed(records[-5:]))
        if not recent:
            lines.append("- No activity recorded in this window.")
        else:
            for record in recent:
                lines.append(
                    f"- [{record.severity}] {record.category} ({record.status}) at {record.occurred_at}: {record.summary}"
                )

        return "\n".join(lines)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ActivitySummaryError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).lower()
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


def _utc_now_iso() -> str:
    return _to_iso(_utc_now())


__all__ = [
    "ActivitySummaryError",
    "AutonomousActivityRecord",
    "AutonomousActivitySummary",
    "AutonomousActivitySummaryGenerator",
    "SummaryPeriod",
]
