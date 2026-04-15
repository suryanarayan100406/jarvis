"""Capability trend dashboard generation with confidence intervals."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from statistics import mean, stdev
from typing import Any, Literal

from .benchmark_harness import BenchmarkHarnessRunResult

TrendDirection = Literal["improving", "stable", "declining"]


@dataclass(frozen=True)
class TrendConfidenceInterval:
    mean_score: float
    lower_bound: float
    upper_bound: float
    margin_of_error: float
    sample_count: int
    standard_deviation: float


@dataclass(frozen=True)
class CapabilityTrendPoint:
    run_id: str
    completed_at: str
    score: float


@dataclass(frozen=True)
class CapabilityTrendSummary:
    capability_id: str
    domain_id: str
    latest_score: float
    baseline_score: float
    delta: float
    direction: TrendDirection
    confidence_interval: TrendConfidenceInterval
    points: tuple[CapabilityTrendPoint, ...]


@dataclass(frozen=True)
class CapabilityTrendDomainSummary:
    domain_id: str
    latest_score: float
    baseline_score: float
    delta: float
    direction: TrendDirection
    confidence_interval: TrendConfidenceInterval
    points: tuple[CapabilityTrendPoint, ...]


@dataclass(frozen=True)
class CapabilityTrendDashboard:
    dashboard_id: str
    generated_at: str
    taxonomy_version: str
    scoring_version: str
    run_count: int
    window_size: int
    baseline_run_id: str
    latest_run_id: str
    overall_latest_score: float
    overall_baseline_score: float
    overall_delta: float
    overall_direction: TrendDirection
    overall_confidence_interval: TrendConfidenceInterval
    domain_summaries: tuple[CapabilityTrendDomainSummary, ...]
    capability_summaries: tuple[CapabilityTrendSummary, ...]
    markdown: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "dashboard_id": self.dashboard_id,
            "generated_at": self.generated_at,
            "taxonomy_version": self.taxonomy_version,
            "scoring_version": self.scoring_version,
            "run_count": self.run_count,
            "window_size": self.window_size,
            "baseline_run_id": self.baseline_run_id,
            "latest_run_id": self.latest_run_id,
            "overall_latest_score": self.overall_latest_score,
            "overall_baseline_score": self.overall_baseline_score,
            "overall_delta": self.overall_delta,
            "overall_direction": self.overall_direction,
            "overall_confidence_interval": _confidence_interval_to_dict(self.overall_confidence_interval),
            "domain_summaries": [
                {
                    "domain_id": summary.domain_id,
                    "latest_score": summary.latest_score,
                    "baseline_score": summary.baseline_score,
                    "delta": summary.delta,
                    "direction": summary.direction,
                    "confidence_interval": _confidence_interval_to_dict(summary.confidence_interval),
                    "points": [
                        {
                            "run_id": point.run_id,
                            "completed_at": point.completed_at,
                            "score": point.score,
                        }
                        for point in summary.points
                    ],
                }
                for summary in sorted(self.domain_summaries, key=lambda item: item.domain_id)
            ],
            "capability_summaries": [
                {
                    "capability_id": summary.capability_id,
                    "domain_id": summary.domain_id,
                    "latest_score": summary.latest_score,
                    "baseline_score": summary.baseline_score,
                    "delta": summary.delta,
                    "direction": summary.direction,
                    "confidence_interval": _confidence_interval_to_dict(summary.confidence_interval),
                    "points": [
                        {
                            "run_id": point.run_id,
                            "completed_at": point.completed_at,
                            "score": point.score,
                        }
                        for point in summary.points
                    ],
                }
                for summary in sorted(self.capability_summaries, key=lambda item: item.capability_id)
            ],
            "markdown": self.markdown,
            "metadata": dict(self.metadata),
        }


class CapabilityTrendError(ValueError):
    """Raised when capability trend dashboard data is invalid."""


class CapabilityTrendDashboardBuilder:
    """Builds confidence-aware trend dashboards from benchmark run histories."""

    def __init__(self, *, window_size: int = 8, confidence_z: float = 1.96) -> None:
        if not isinstance(window_size, int):
            raise TypeError("window_size must be an integer")
        if window_size < 1:
            raise CapabilityTrendError("window_size must be at least 1")

        try:
            normalized_confidence_z = float(confidence_z)
        except Exception as exc:
            raise CapabilityTrendError("confidence_z must be numeric") from exc
        if normalized_confidence_z <= 0:
            raise CapabilityTrendError("confidence_z must be positive")

        self.window_size = window_size
        self.confidence_z = normalized_confidence_z

    def build_dashboard(
        self,
        runs: list[BenchmarkHarnessRunResult] | tuple[BenchmarkHarnessRunResult, ...],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> CapabilityTrendDashboard:
        normalized_runs = self._normalize_runs(runs)
        windowed_runs = normalized_runs[-self.window_size :]

        baseline = windowed_runs[0]
        latest = windowed_runs[-1]

        overall_points = tuple(
            CapabilityTrendPoint(
                run_id=run.run_id,
                completed_at=run.completed_at,
                score=round(float(run.overall_score), 12),
            )
            for run in windowed_runs
        )
        overall_confidence_interval = _build_confidence_interval(overall_points, z=self.confidence_z)
        overall_latest_score = overall_points[-1].score
        overall_baseline_score = overall_points[0].score
        overall_delta = round(overall_latest_score - overall_baseline_score, 12)
        overall_direction = _derive_direction(overall_delta)

        domain_ids = [domain.domain_id for domain in baseline.domain_scores]
        domain_summaries: list[CapabilityTrendDomainSummary] = []
        capability_summaries: list[CapabilityTrendSummary] = []

        for domain_id in sorted(domain_ids):
            domain_points = tuple(
                CapabilityTrendPoint(
                    run_id=run.run_id,
                    completed_at=run.completed_at,
                    score=round(float(_get_domain_score(run, domain_id).weighted_score), 12),
                )
                for run in windowed_runs
            )
            domain_confidence_interval = _build_confidence_interval(domain_points, z=self.confidence_z)
            domain_latest_score = domain_points[-1].score
            domain_baseline_score = domain_points[0].score
            domain_delta = round(domain_latest_score - domain_baseline_score, 12)
            domain_direction = _derive_direction(domain_delta)

            domain_summaries.append(
                CapabilityTrendDomainSummary(
                    domain_id=domain_id,
                    latest_score=domain_latest_score,
                    baseline_score=domain_baseline_score,
                    delta=domain_delta,
                    direction=domain_direction,
                    confidence_interval=domain_confidence_interval,
                    points=domain_points,
                )
            )

            capability_ids = [
                capability.capability_id
                for capability in _get_domain_score(baseline, domain_id).capability_scores
            ]
            for capability_id in sorted(capability_ids):
                capability_points = tuple(
                    CapabilityTrendPoint(
                        run_id=run.run_id,
                        completed_at=run.completed_at,
                        score=round(float(_get_capability_score(run, domain_id, capability_id).weighted_score), 12),
                    )
                    for run in windowed_runs
                )
                capability_confidence_interval = _build_confidence_interval(capability_points, z=self.confidence_z)
                capability_latest_score = capability_points[-1].score
                capability_baseline_score = capability_points[0].score
                capability_delta = round(capability_latest_score - capability_baseline_score, 12)
                capability_direction = _derive_direction(capability_delta)

                capability_summaries.append(
                    CapabilityTrendSummary(
                        capability_id=capability_id,
                        domain_id=domain_id,
                        latest_score=capability_latest_score,
                        baseline_score=capability_baseline_score,
                        delta=capability_delta,
                        direction=capability_direction,
                        confidence_interval=capability_confidence_interval,
                        points=capability_points,
                    )
                )

        dashboard_id = _build_dashboard_id(
            runs=windowed_runs,
            window_size=self.window_size,
            confidence_z=self.confidence_z,
        )
        markdown = _render_markdown(
            overall_latest_score=overall_latest_score,
            overall_baseline_score=overall_baseline_score,
            overall_delta=overall_delta,
            overall_direction=overall_direction,
            overall_confidence_interval=overall_confidence_interval,
            domain_summaries=domain_summaries,
            capability_summaries=capability_summaries,
            baseline_run_id=baseline.run_id,
            latest_run_id=latest.run_id,
        )

        return CapabilityTrendDashboard(
            dashboard_id=dashboard_id,
            generated_at=_utc_now_iso(),
            taxonomy_version=baseline.taxonomy_version,
            scoring_version=baseline.scoring_version,
            run_count=len(windowed_runs),
            window_size=self.window_size,
            baseline_run_id=baseline.run_id,
            latest_run_id=latest.run_id,
            overall_latest_score=overall_latest_score,
            overall_baseline_score=overall_baseline_score,
            overall_delta=overall_delta,
            overall_direction=overall_direction,
            overall_confidence_interval=overall_confidence_interval,
            domain_summaries=tuple(sorted(domain_summaries, key=lambda item: item.domain_id)),
            capability_summaries=tuple(sorted(capability_summaries, key=lambda item: item.capability_id)),
            markdown=markdown,
            metadata=dict(metadata or {}),
        )

    def _normalize_runs(
        self,
        runs: list[BenchmarkHarnessRunResult] | tuple[BenchmarkHarnessRunResult, ...],
    ) -> tuple[BenchmarkHarnessRunResult, ...]:
        if not runs:
            raise CapabilityTrendError("runs must include at least one benchmark result")

        normalized_runs: list[BenchmarkHarnessRunResult] = []
        for run in runs:
            if not isinstance(run, BenchmarkHarnessRunResult):
                raise TypeError("runs must contain BenchmarkHarnessRunResult entries")
            normalized_runs.append(run)

        normalized_runs.sort(key=lambda item: (_parse_iso(item.completed_at), item.run_id))

        reference = normalized_runs[0]
        reference_domain_ids = tuple(sorted(domain.domain_id for domain in reference.domain_scores))
        reference_capability_ids = tuple(
            sorted(
                capability.capability_id
                for domain in reference.domain_scores
                for capability in domain.capability_scores
            )
        )

        for run in normalized_runs:
            if run.taxonomy_version != reference.taxonomy_version:
                raise CapabilityTrendError("All runs must share taxonomy_version")
            if run.scoring_version != reference.scoring_version:
                raise CapabilityTrendError("All runs must share scoring_version")
            if not run.strict_coverage:
                raise CapabilityTrendError("All runs must have strict_coverage enabled")

            domain_ids = tuple(sorted(domain.domain_id for domain in run.domain_scores))
            if domain_ids != reference_domain_ids:
                raise CapabilityTrendError("All runs must contain the same domain coverage")

            capability_ids = tuple(
                sorted(
                    capability.capability_id
                    for domain in run.domain_scores
                    for capability in domain.capability_scores
                )
            )
            if capability_ids != reference_capability_ids:
                raise CapabilityTrendError("All runs must contain the same capability coverage")

        return tuple(normalized_runs)


def _get_domain_score(run: BenchmarkHarnessRunResult, domain_id: str):
    for domain in run.domain_scores:
        if domain.domain_id == domain_id:
            return domain
    raise CapabilityTrendError(f"Run {run.run_id} is missing domain {domain_id}")


def _get_capability_score(run: BenchmarkHarnessRunResult, domain_id: str, capability_id: str):
    domain = _get_domain_score(run, domain_id)
    for capability in domain.capability_scores:
        if capability.capability_id == capability_id:
            return capability
    raise CapabilityTrendError(
        f"Run {run.run_id} domain {domain_id} is missing capability {capability_id}"
    )


def _build_confidence_interval(
    points: tuple[CapabilityTrendPoint, ...],
    *,
    z: float,
) -> TrendConfidenceInterval:
    values = [point.score for point in points]
    sample_count = len(values)
    average = round(float(mean(values)), 12)

    if sample_count < 2:
        return TrendConfidenceInterval(
            mean_score=average,
            lower_bound=average,
            upper_bound=average,
            margin_of_error=0.0,
            sample_count=sample_count,
            standard_deviation=0.0,
        )

    standard_deviation = float(stdev(values))
    margin = z * standard_deviation / math.sqrt(sample_count)
    lower = max(0.0, average - margin)
    upper = min(1.0, average + margin)

    return TrendConfidenceInterval(
        mean_score=average,
        lower_bound=round(lower, 12),
        upper_bound=round(upper, 12),
        margin_of_error=round(margin, 12),
        sample_count=sample_count,
        standard_deviation=round(standard_deviation, 12),
    )


def _derive_direction(delta: float) -> TrendDirection:
    if delta > 0.005:
        return "improving"
    if delta < -0.005:
        return "declining"
    return "stable"


def _build_dashboard_id(
    *,
    runs: tuple[BenchmarkHarnessRunResult, ...],
    window_size: int,
    confidence_z: float,
) -> str:
    canonical = json.dumps(
        {
            "run_ids": [run.run_id for run in runs],
            "run_digests": [run.deterministic_digest for run in runs],
            "window_size": window_size,
            "confidence_z": confidence_z,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"trend-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _render_markdown(
    *,
    overall_latest_score: float,
    overall_baseline_score: float,
    overall_delta: float,
    overall_direction: TrendDirection,
    overall_confidence_interval: TrendConfidenceInterval,
    domain_summaries: list[CapabilityTrendDomainSummary],
    capability_summaries: list[CapabilityTrendSummary],
    baseline_run_id: str,
    latest_run_id: str,
) -> str:
    lines = [
        "# Capability Trend Dashboard",
        "",
        f"Baseline run: {baseline_run_id}",
        f"Latest run: {latest_run_id}",
        f"Overall baseline score: {overall_baseline_score:.4f}",
        f"Overall latest score: {overall_latest_score:.4f}",
        f"Overall delta: {overall_delta:+.4f} ({overall_direction})",
        (
            "Overall confidence interval: "
            f"[{overall_confidence_interval.lower_bound:.4f}, {overall_confidence_interval.upper_bound:.4f}]"
        ),
        "",
        "## Domain Trends",
    ]

    for summary in sorted(domain_summaries, key=lambda item: item.domain_id):
        lines.append(
            (
                f"- {summary.domain_id}: latest={summary.latest_score:.4f}, "
                f"delta={summary.delta:+.4f}, direction={summary.direction}, "
                f"ci=[{summary.confidence_interval.lower_bound:.4f}, {summary.confidence_interval.upper_bound:.4f}]"
            )
        )

    lines.append("")
    lines.append("## Capability Highlights")

    top_movers = sorted(
        capability_summaries,
        key=lambda item: abs(item.delta),
        reverse=True,
    )[:6]

    if not top_movers:
        lines.append("- No capability trend data available.")
    else:
        for summary in top_movers:
            lines.append(
                (
                    f"- {summary.capability_id} ({summary.domain_id}): "
                    f"latest={summary.latest_score:.4f}, delta={summary.delta:+.4f}, "
                    f"direction={summary.direction}"
                )
            )

    return "\n".join(lines)


def _confidence_interval_to_dict(value: TrendConfidenceInterval) -> dict[str, Any]:
    return {
        "mean_score": value.mean_score,
        "lower_bound": value.lower_bound,
        "upper_bound": value.upper_bound,
        "margin_of_error": value.margin_of_error,
        "sample_count": value.sample_count,
        "standard_deviation": value.standard_deviation,
    }


def _parse_iso(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "TrendConfidenceInterval",
    "CapabilityTrendPoint",
    "CapabilityTrendSummary",
    "CapabilityTrendDomainSummary",
    "CapabilityTrendDashboard",
    "CapabilityTrendDashboardBuilder",
    "CapabilityTrendError",
]
