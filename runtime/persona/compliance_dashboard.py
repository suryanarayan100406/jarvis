"""Compliance dashboard generation with trend and drift alert reporting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from statistics import mean
from typing import Any, Literal

TrendDirection = Literal["improving", "stable", "declining"]
DashboardStatus = Literal["healthy", "warning", "critical"]
DriftSeverity = Literal["warning", "critical"]


@dataclass(frozen=True)
class ComplianceSignalSnapshot:
    snapshot_id: str
    component_id: str
    score: float
    recorded_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ComplianceTrendPoint:
    snapshot_id: str
    recorded_at: str
    score: float


@dataclass(frozen=True)
class ComplianceTrendSummary:
    component_id: str
    latest_score: float
    baseline_score: float
    delta: float
    moving_average: float
    direction: TrendDirection
    points: tuple[ComplianceTrendPoint, ...]


@dataclass(frozen=True)
class ComplianceDriftAlert:
    alert_id: str
    component_id: str
    severity: DriftSeverity
    delta: float
    latest_score: float
    baseline_score: float
    threshold: float
    reason: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ComplianceDashboard:
    dashboard_id: str
    generated_at: str
    window_size: int
    component_count: int
    snapshot_count: int
    overall_latest_score: float
    overall_baseline_score: float
    overall_delta: float
    overall_direction: TrendDirection
    overall_status: DashboardStatus
    trend_summaries: tuple[ComplianceTrendSummary, ...]
    drift_alerts: tuple[ComplianceDriftAlert, ...]
    markdown: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "dashboard_id": self.dashboard_id,
            "generated_at": self.generated_at,
            "window_size": self.window_size,
            "component_count": self.component_count,
            "snapshot_count": self.snapshot_count,
            "overall_latest_score": self.overall_latest_score,
            "overall_baseline_score": self.overall_baseline_score,
            "overall_delta": self.overall_delta,
            "overall_direction": self.overall_direction,
            "overall_status": self.overall_status,
            "trend_summaries": [
                {
                    "component_id": summary.component_id,
                    "latest_score": summary.latest_score,
                    "baseline_score": summary.baseline_score,
                    "delta": summary.delta,
                    "moving_average": summary.moving_average,
                    "direction": summary.direction,
                    "points": [
                        {
                            "snapshot_id": point.snapshot_id,
                            "recorded_at": point.recorded_at,
                            "score": point.score,
                        }
                        for point in summary.points
                    ],
                }
                for summary in sorted(self.trend_summaries, key=lambda item: item.component_id)
            ],
            "drift_alerts": [
                {
                    "alert_id": alert.alert_id,
                    "component_id": alert.component_id,
                    "severity": alert.severity,
                    "delta": alert.delta,
                    "latest_score": alert.latest_score,
                    "baseline_score": alert.baseline_score,
                    "threshold": alert.threshold,
                    "reason": alert.reason,
                    "metadata": dict(alert.metadata),
                }
                for alert in sorted(self.drift_alerts, key=lambda item: item.alert_id)
            ],
            "markdown": self.markdown,
            "metadata": dict(self.metadata),
        }


class ComplianceDashboardError(ValueError):
    """Raised when dashboard inputs violate compliance dashboard constraints."""


class ComplianceDashboardBuilder:
    """Builds deterministic compliance dashboards with drift alerts."""

    def __init__(
        self,
        *,
        window_size: int = 8,
        warning_drift_threshold: float = 0.05,
        critical_drift_threshold: float = 0.12,
        stable_delta: float = 0.01,
    ) -> None:
        if not isinstance(window_size, int):
            raise TypeError("window_size must be an integer")
        if window_size < 1:
            raise ComplianceDashboardError("window_size must be at least 1")

        warning = _normalize_threshold(warning_drift_threshold, "warning_drift_threshold")
        critical = _normalize_threshold(critical_drift_threshold, "critical_drift_threshold")
        stable = _normalize_threshold(stable_delta, "stable_delta")

        if critical < warning:
            raise ComplianceDashboardError(
                "critical_drift_threshold must be greater than or equal to warning_drift_threshold"
            )
        if stable >= warning:
            raise ComplianceDashboardError("stable_delta must be less than warning_drift_threshold")

        self.window_size = window_size
        self.warning_drift_threshold = warning
        self.critical_drift_threshold = critical
        self.stable_delta = stable

    def build_dashboard(
        self,
        history_by_component: dict[str, list[ComplianceSignalSnapshot] | tuple[ComplianceSignalSnapshot, ...]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ComplianceDashboard:
        if not isinstance(history_by_component, dict):
            raise TypeError("history_by_component must be a dict")
        if not history_by_component:
            raise ComplianceDashboardError("history_by_component must include at least one component")

        trend_summaries: list[ComplianceTrendSummary] = []
        drift_alerts: list[ComplianceDriftAlert] = []
        total_snapshots = 0
        seen_components: set[str] = set()

        for component_id, snapshots in history_by_component.items():
            normalized_component = _normalize_required(component_id, "component_id").lower()
            if normalized_component in seen_components:
                raise ComplianceDashboardError(f"Duplicate component_id: {normalized_component}")
            seen_components.add(normalized_component)

            normalized_snapshots = _normalize_snapshots(
                snapshots,
                expected_component_id=normalized_component,
            )
            windowed = normalized_snapshots[-self.window_size :]
            total_snapshots += len(windowed)

            points = tuple(
                ComplianceTrendPoint(
                    snapshot_id=snapshot.snapshot_id,
                    recorded_at=snapshot.recorded_at,
                    score=snapshot.score,
                )
                for snapshot in windowed
            )

            baseline_score = points[0].score
            latest_score = points[-1].score
            delta = round(latest_score - baseline_score, 12)
            moving_average = round(float(mean(point.score for point in points)), 12)
            direction = _derive_direction(delta, stable_delta=self.stable_delta)

            trend_summary = ComplianceTrendSummary(
                component_id=normalized_component,
                latest_score=latest_score,
                baseline_score=baseline_score,
                delta=delta,
                moving_average=moving_average,
                direction=direction,
                points=points,
            )
            trend_summaries.append(trend_summary)

            alert = self._build_alert_if_needed(trend_summary)
            if alert is not None:
                drift_alerts.append(alert)

        sorted_trends = tuple(sorted(trend_summaries, key=lambda item: item.component_id))
        sorted_alerts = tuple(sorted(drift_alerts, key=lambda item: item.alert_id))

        overall_latest_score = round(
            float(mean(summary.latest_score for summary in sorted_trends)),
            12,
        )
        overall_baseline_score = round(
            float(mean(summary.baseline_score for summary in sorted_trends)),
            12,
        )
        overall_delta = round(overall_latest_score - overall_baseline_score, 12)
        overall_direction = _derive_direction(overall_delta, stable_delta=self.stable_delta)
        overall_status = _derive_overall_status(sorted_alerts)

        dashboard_id = _build_dashboard_id(
            trend_summaries=sorted_trends,
            drift_alerts=sorted_alerts,
            window_size=self.window_size,
            warning_drift_threshold=self.warning_drift_threshold,
            critical_drift_threshold=self.critical_drift_threshold,
            stable_delta=self.stable_delta,
        )
        markdown = _render_markdown(
            overall_latest_score=overall_latest_score,
            overall_baseline_score=overall_baseline_score,
            overall_delta=overall_delta,
            overall_direction=overall_direction,
            overall_status=overall_status,
            trend_summaries=sorted_trends,
            drift_alerts=sorted_alerts,
        )

        return ComplianceDashboard(
            dashboard_id=dashboard_id,
            generated_at=_utc_now_iso(),
            window_size=self.window_size,
            component_count=len(sorted_trends),
            snapshot_count=total_snapshots,
            overall_latest_score=overall_latest_score,
            overall_baseline_score=overall_baseline_score,
            overall_delta=overall_delta,
            overall_direction=overall_direction,
            overall_status=overall_status,
            trend_summaries=sorted_trends,
            drift_alerts=sorted_alerts,
            markdown=markdown,
            metadata=dict(metadata or {}),
        )

    def _build_alert_if_needed(self, summary: ComplianceTrendSummary) -> ComplianceDriftAlert | None:
        if summary.delta <= -self.critical_drift_threshold:
            severity: DriftSeverity = "critical"
            threshold = self.critical_drift_threshold
        elif summary.delta <= -self.warning_drift_threshold:
            severity = "warning"
            threshold = self.warning_drift_threshold
        else:
            return None

        canonical = json.dumps(
            {
                "component_id": summary.component_id,
                "severity": severity,
                "delta": summary.delta,
                "latest_score": summary.latest_score,
                "baseline_score": summary.baseline_score,
                "threshold": threshold,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = sha256(canonical.encode("utf-8")).hexdigest()

        return ComplianceDriftAlert(
            alert_id=f"drift-{digest[:20]}",
            component_id=summary.component_id,
            severity=severity,
            delta=summary.delta,
            latest_score=summary.latest_score,
            baseline_score=summary.baseline_score,
            threshold=threshold,
            reason=(
                f"Compliance score drifted from {summary.baseline_score:.4f} to {summary.latest_score:.4f} "
                f"(delta {summary.delta:.4f})."
            ),
            metadata={"direction": summary.direction},
        )


def _normalize_snapshots(
    snapshots: list[ComplianceSignalSnapshot] | tuple[ComplianceSignalSnapshot, ...],
    *,
    expected_component_id: str,
) -> tuple[ComplianceSignalSnapshot, ...]:
    if not isinstance(snapshots, (list, tuple)):
        raise TypeError("component history must be a list or tuple of ComplianceSignalSnapshot")
    if not snapshots:
        raise ComplianceDashboardError(f"Component {expected_component_id} must include at least one snapshot")

    normalized: list[ComplianceSignalSnapshot] = []
    seen_snapshot_ids: set[str] = set()

    for snapshot in snapshots:
        if not isinstance(snapshot, ComplianceSignalSnapshot):
            raise TypeError("component history entries must be ComplianceSignalSnapshot")

        snapshot_id = _normalize_required(snapshot.snapshot_id, "snapshot_id")
        if snapshot_id in seen_snapshot_ids:
            raise ComplianceDashboardError(f"Duplicate snapshot_id in {expected_component_id}: {snapshot_id}")
        seen_snapshot_ids.add(snapshot_id)

        component_id = _normalize_required(snapshot.component_id, "component_id").lower()
        if component_id != expected_component_id:
            raise ComplianceDashboardError(
                f"Snapshot {snapshot_id} component_id {component_id} does not match expected {expected_component_id}"
            )

        normalized.append(
            ComplianceSignalSnapshot(
                snapshot_id=snapshot_id,
                component_id=component_id,
                score=_normalize_score(snapshot.score, "score"),
                recorded_at=_to_iso(_parse_iso(snapshot.recorded_at)),
                metadata=dict(snapshot.metadata),
            )
        )

    normalized.sort(key=lambda item: (_parse_iso(item.recorded_at), item.snapshot_id))
    return tuple(normalized)


def _normalize_threshold(value: float, field_name: str) -> float:
    try:
        normalized = float(value)
    except Exception as exc:
        raise ComplianceDashboardError(f"{field_name} must be numeric") from exc
    if normalized <= 0:
        raise ComplianceDashboardError(f"{field_name} must be positive")
    return round(normalized, 12)


def _normalize_score(value: float, field_name: str) -> float:
    try:
        numeric = float(value)
    except Exception as exc:
        raise ComplianceDashboardError(f"{field_name} must be numeric") from exc
    if numeric < 0 or numeric > 1:
        raise ComplianceDashboardError(f"{field_name} must be in range [0, 1]")
    return round(numeric, 12)


def _derive_direction(delta: float, *, stable_delta: float) -> TrendDirection:
    if delta > stable_delta:
        return "improving"
    if delta < -stable_delta:
        return "declining"
    return "stable"


def _derive_overall_status(alerts: tuple[ComplianceDriftAlert, ...]) -> DashboardStatus:
    if any(alert.severity == "critical" for alert in alerts):
        return "critical"
    if any(alert.severity == "warning" for alert in alerts):
        return "warning"
    return "healthy"


def _build_dashboard_id(
    *,
    trend_summaries: tuple[ComplianceTrendSummary, ...],
    drift_alerts: tuple[ComplianceDriftAlert, ...],
    window_size: int,
    warning_drift_threshold: float,
    critical_drift_threshold: float,
    stable_delta: float,
) -> str:
    canonical = json.dumps(
        {
            "window_size": window_size,
            "warning_drift_threshold": warning_drift_threshold,
            "critical_drift_threshold": critical_drift_threshold,
            "stable_delta": stable_delta,
            "trend_summaries": [
                {
                    "component_id": summary.component_id,
                    "baseline_score": summary.baseline_score,
                    "latest_score": summary.latest_score,
                    "delta": summary.delta,
                    "points": [
                        {
                            "snapshot_id": point.snapshot_id,
                            "recorded_at": point.recorded_at,
                            "score": point.score,
                        }
                        for point in summary.points
                    ],
                }
                for summary in trend_summaries
            ],
            "drift_alerts": [
                {
                    "alert_id": alert.alert_id,
                    "component_id": alert.component_id,
                    "severity": alert.severity,
                    "delta": alert.delta,
                }
                for alert in drift_alerts
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"compliance-dashboard-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _render_markdown(
    *,
    overall_latest_score: float,
    overall_baseline_score: float,
    overall_delta: float,
    overall_direction: TrendDirection,
    overall_status: DashboardStatus,
    trend_summaries: tuple[ComplianceTrendSummary, ...],
    drift_alerts: tuple[ComplianceDriftAlert, ...],
) -> str:
    lines = [
        "# Compliance Dashboard",
        "",
        f"Overall status: {overall_status}",
        f"Overall latest score: {overall_latest_score:.4f}",
        f"Overall baseline score: {overall_baseline_score:.4f}",
        f"Overall delta: {overall_delta:.4f} ({overall_direction})",
        "",
        "## Trend Summaries",
    ]

    for summary in trend_summaries:
        lines.append(
            (
                f"- {summary.component_id}: latest={summary.latest_score:.4f}, "
                f"baseline={summary.baseline_score:.4f}, delta={summary.delta:.4f}, "
                f"direction={summary.direction}, moving_average={summary.moving_average:.4f}"
            )
        )

    lines.append("")
    lines.append("## Drift Alerts")
    if not drift_alerts:
        lines.append("- none")
    else:
        for alert in drift_alerts:
            lines.append(
                (
                    f"- {alert.component_id}: severity={alert.severity}, "
                    f"delta={alert.delta:.4f}, threshold={alert.threshold:.4f}, "
                    f"latest={alert.latest_score:.4f}"
                )
            )

    return "\n".join(lines)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ComplianceDashboardError(f"{field_name} is required")
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "TrendDirection",
    "DashboardStatus",
    "DriftSeverity",
    "ComplianceSignalSnapshot",
    "ComplianceTrendPoint",
    "ComplianceTrendSummary",
    "ComplianceDriftAlert",
    "ComplianceDashboard",
    "ComplianceDashboardError",
    "ComplianceDashboardBuilder",
]
