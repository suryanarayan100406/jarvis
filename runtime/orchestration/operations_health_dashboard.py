"""Operational dashboard generation for runtime, autonomy, and security health."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

HealthStatus = Literal["healthy", "warning", "critical"]
MetricDirection = Literal["higher_is_better", "lower_is_better"]

_REQUIRED_DOMAINS = {"runtime", "autonomy", "security"}


@dataclass(frozen=True)
class OperationalHealthMetric:
    metric_id: str
    value: float
    target: float
    direction: MetricDirection
    warning_floor: float
    critical_floor: float
    weight: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OperationalHealthMetricResult:
    metric_id: str
    status: HealthStatus
    score: float
    value: float
    target: float
    direction: MetricDirection
    weight: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OperationalHealthDomainSnapshot:
    domain_id: str
    status: HealthStatus
    weighted_score: float
    metric_count: int
    metric_results: tuple[OperationalHealthMetricResult, ...]


@dataclass(frozen=True)
class OperationsHealthDashboard:
    dashboard_id: str
    generated_at: str
    window_id: str
    overall_status: HealthStatus
    overall_score: float
    domain_snapshots: tuple[OperationalHealthDomainSnapshot, ...]
    markdown: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "dashboard_id": self.dashboard_id,
            "generated_at": self.generated_at,
            "window_id": self.window_id,
            "overall_status": self.overall_status,
            "overall_score": self.overall_score,
            "domain_snapshots": [
                {
                    "domain_id": domain.domain_id,
                    "status": domain.status,
                    "weighted_score": domain.weighted_score,
                    "metric_count": domain.metric_count,
                    "metric_results": [
                        {
                            "metric_id": result.metric_id,
                            "status": result.status,
                            "score": result.score,
                            "value": result.value,
                            "target": result.target,
                            "direction": result.direction,
                            "weight": result.weight,
                            "metadata": dict(result.metadata),
                        }
                        for result in sorted(domain.metric_results, key=lambda item: item.metric_id)
                    ],
                }
                for domain in sorted(self.domain_snapshots, key=lambda item: item.domain_id)
            ],
            "markdown": self.markdown,
            "metadata": dict(self.metadata),
        }


class OperationsHealthDashboardError(ValueError):
    """Raised when dashboard inputs violate operational constraints."""


class OperationsHealthDashboardBuilder:
    """Builds a deterministic health dashboard from operational metrics."""

    def __init__(self, *, domain_weights: dict[str, float] | None = None) -> None:
        if domain_weights is None:
            domain_weights = {
                "runtime": 0.4,
                "autonomy": 0.35,
                "security": 0.25,
            }

        normalized_weights: dict[str, float] = {}
        for domain_id, weight in domain_weights.items():
            normalized_domain_id = _normalize_required(domain_id, "domain_id").lower()
            normalized_weights[normalized_domain_id] = _normalize_positive(weight, "domain_weight")

        missing_domains = sorted(_REQUIRED_DOMAINS - set(normalized_weights))
        if missing_domains:
            raise OperationsHealthDashboardError(
                "domain_weights missing required domains: " + ", ".join(missing_domains)
            )

        self.domain_weights = normalized_weights

    def build_dashboard(
        self,
        domain_metrics: dict[str, list[OperationalHealthMetric] | tuple[OperationalHealthMetric, ...]],
        *,
        window_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> OperationsHealthDashboard:
        normalized_window_id = _normalize_required(window_id, "window_id")

        normalized_domain_metrics: dict[str, tuple[OperationalHealthMetric, ...]] = {}
        for domain_id, metrics in domain_metrics.items():
            normalized_domain_id = _normalize_required(domain_id, "domain_id").lower()
            normalized_domain_metrics[normalized_domain_id] = _normalize_metrics(metrics, normalized_domain_id)

        missing_domains = sorted(_REQUIRED_DOMAINS - set(normalized_domain_metrics))
        if missing_domains:
            raise OperationsHealthDashboardError(
                "domain_metrics missing required domains: " + ", ".join(missing_domains)
            )

        domain_snapshots: list[OperationalHealthDomainSnapshot] = []
        for domain_id in sorted(_REQUIRED_DOMAINS):
            metric_results = tuple(
                _evaluate_metric(metric)
                for metric in sorted(normalized_domain_metrics[domain_id], key=lambda item: item.metric_id)
            )

            weight_denominator = sum(result.weight for result in metric_results)
            if weight_denominator <= 0:
                raise OperationsHealthDashboardError(
                    f"Domain {domain_id} metric weight denominator must be positive"
                )

            weighted_score = round(
                sum(result.score * result.weight for result in metric_results) / weight_denominator,
                12,
            )

            domain_status = _merge_statuses(result.status for result in metric_results)
            domain_snapshots.append(
                OperationalHealthDomainSnapshot(
                    domain_id=domain_id,
                    status=domain_status,
                    weighted_score=weighted_score,
                    metric_count=len(metric_results),
                    metric_results=metric_results,
                )
            )

        overall_weight_denominator = sum(
            self.domain_weights[domain.domain_id] for domain in domain_snapshots
        )
        overall_score = round(
            sum(
                domain.weighted_score * self.domain_weights[domain.domain_id]
                for domain in domain_snapshots
            )
            / overall_weight_denominator,
            12,
        )
        overall_status = _merge_statuses(domain.status for domain in domain_snapshots)

        dashboard_id = _build_dashboard_id(
            window_id=normalized_window_id,
            domain_snapshots=domain_snapshots,
            domain_weights=self.domain_weights,
        )

        markdown = _render_markdown(
            window_id=normalized_window_id,
            overall_status=overall_status,
            overall_score=overall_score,
            domain_snapshots=domain_snapshots,
        )

        return OperationsHealthDashboard(
            dashboard_id=dashboard_id,
            generated_at=_utc_now_iso(),
            window_id=normalized_window_id,
            overall_status=overall_status,
            overall_score=overall_score,
            domain_snapshots=tuple(sorted(domain_snapshots, key=lambda item: item.domain_id)),
            markdown=markdown,
            metadata=dict(metadata or {}),
        )


def _normalize_metrics(
    metrics: list[OperationalHealthMetric] | tuple[OperationalHealthMetric, ...],
    domain_id: str,
) -> tuple[OperationalHealthMetric, ...]:
    if not metrics:
        raise OperationsHealthDashboardError(f"Domain {domain_id} must include at least one metric")

    normalized: list[OperationalHealthMetric] = []
    seen_metric_ids: set[str] = set()
    for metric in metrics:
        if not isinstance(metric, OperationalHealthMetric):
            raise TypeError("domain metrics must contain OperationalHealthMetric entries")

        metric_id = _normalize_required(metric.metric_id, f"{domain_id}.metric_id").lower()
        if metric_id in seen_metric_ids:
            raise OperationsHealthDashboardError(f"Duplicate metric_id in {domain_id}: {metric_id}")
        seen_metric_ids.add(metric_id)

        direction = _normalize_required(metric.direction, f"{metric_id}.direction").lower()
        if direction not in {"higher_is_better", "lower_is_better"}:
            raise OperationsHealthDashboardError(
                f"Unsupported direction {direction} for metric {metric_id}"
            )

        warning_floor = _normalize_ratio(metric.warning_floor, f"{metric_id}.warning_floor")
        critical_floor = _normalize_ratio(metric.critical_floor, f"{metric_id}.critical_floor")
        if critical_floor > warning_floor:
            raise OperationsHealthDashboardError(
                f"{metric_id}.critical_floor must be <= warning_floor"
            )

        normalized.append(
            OperationalHealthMetric(
                metric_id=metric_id,
                value=float(metric.value),
                target=float(metric.target),
                direction=direction,
                warning_floor=warning_floor,
                critical_floor=critical_floor,
                weight=_normalize_positive(metric.weight, f"{metric_id}.weight"),
                metadata=dict(metric.metadata),
            )
        )

    return tuple(sorted(normalized, key=lambda item: item.metric_id))


def _evaluate_metric(metric: OperationalHealthMetric) -> OperationalHealthMetricResult:
    if metric.target <= 0:
        raise OperationsHealthDashboardError(f"{metric.metric_id}.target must be positive")

    if metric.direction == "higher_is_better":
        score = min(1.0, max(0.0, metric.value / metric.target))
    else:
        score = min(1.0, max(0.0, metric.target / metric.value))
    score = round(score, 12)

    if score < metric.critical_floor:
        status: HealthStatus = "critical"
    elif score < metric.warning_floor:
        status = "warning"
    else:
        status = "healthy"

    return OperationalHealthMetricResult(
        metric_id=metric.metric_id,
        status=status,
        score=score,
        value=metric.value,
        target=metric.target,
        direction=metric.direction,
        weight=metric.weight,
        metadata=dict(metric.metadata),
    )


def _merge_statuses(statuses) -> HealthStatus:
    rank = {"healthy": 0, "warning": 1, "critical": 2}
    resolved = "healthy"
    max_rank = -1
    for status in statuses:
        status_rank = rank[status]
        if status_rank > max_rank:
            max_rank = status_rank
            resolved = status
    return resolved


def _build_dashboard_id(
    *,
    window_id: str,
    domain_snapshots: list[OperationalHealthDomainSnapshot],
    domain_weights: dict[str, float],
) -> str:
    canonical = json.dumps(
        {
            "window_id": window_id,
            "domain_weights": domain_weights,
            "domain_snapshots": [
                {
                    "domain_id": domain.domain_id,
                    "status": domain.status,
                    "weighted_score": domain.weighted_score,
                    "metrics": [
                        {
                            "metric_id": result.metric_id,
                            "status": result.status,
                            "score": result.score,
                        }
                        for result in sorted(domain.metric_results, key=lambda item: item.metric_id)
                    ],
                }
                for domain in sorted(domain_snapshots, key=lambda item: item.domain_id)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"ops-health-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _render_markdown(
    *,
    window_id: str,
    overall_status: HealthStatus,
    overall_score: float,
    domain_snapshots: list[OperationalHealthDomainSnapshot],
) -> str:
    lines = [
        "# Operations Health Dashboard",
        "",
        f"Window: {window_id}",
        f"Overall status: {overall_status}",
        f"Overall score: {overall_score:.4f}",
        "",
        "## Domain Health",
    ]

    for domain in sorted(domain_snapshots, key=lambda item: item.domain_id):
        lines.append(
            f"- {domain.domain_id}: status={domain.status}, score={domain.weighted_score:.4f}"
        )

    lines.append("")
    lines.append("## Metric Highlights")
    for domain in sorted(domain_snapshots, key=lambda item: item.domain_id):
        for result in sorted(domain.metric_results, key=lambda item: item.metric_id):
            lines.append(
                (
                    f"- {domain.domain_id}.{result.metric_id}: status={result.status}, "
                    f"score={result.score:.4f}, value={result.value:.4f}, target={result.target:.4f}"
                )
            )

    return "\n".join(lines)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise OperationsHealthDashboardError(f"{field_name} is required")
    return normalized


def _normalize_ratio(value: float, field_name: str) -> float:
    try:
        normalized = float(value)
    except Exception as exc:
        raise OperationsHealthDashboardError(f"{field_name} must be numeric") from exc
    if normalized < 0 or normalized > 1:
        raise OperationsHealthDashboardError(f"{field_name} must be between 0 and 1")
    return round(normalized, 12)


def _normalize_positive(value: float, field_name: str) -> float:
    try:
        normalized = float(value)
    except Exception as exc:
        raise OperationsHealthDashboardError(f"{field_name} must be numeric") from exc
    if normalized <= 0:
        raise OperationsHealthDashboardError(f"{field_name} must be positive")
    return round(normalized, 12)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "HealthStatus",
    "MetricDirection",
    "OperationalHealthMetric",
    "OperationalHealthMetricResult",
    "OperationalHealthDomainSnapshot",
    "OperationsHealthDashboard",
    "OperationsHealthDashboardError",
    "OperationsHealthDashboardBuilder",
]