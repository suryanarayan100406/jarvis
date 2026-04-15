"""Quarterly moonshot gap-report generation against capability targets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from .benchmark_harness import BenchmarkHarnessRunResult
from .failure_taxonomy import FailureLabelingReport, FailureRootCauseLabel

GapStatus = Literal["met", "near", "off_track"]
GapRiskLevel = Literal["low", "moderate", "elevated"]


@dataclass(frozen=True)
class MoonshotDomainTarget:
    domain_id: str
    target_score: float
    near_threshold: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MoonshotCapabilityTarget:
    capability_id: str
    domain_id: str
    target_score: float
    near_threshold: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MoonshotTargetProfile:
    target_id: str
    target_version: str
    quarter_id: str
    taxonomy_version: str
    created_at: str
    overall_target_score: float
    near_threshold: float
    domain_targets: tuple[MoonshotDomainTarget, ...]
    capability_targets: tuple[MoonshotCapabilityTarget, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "target_version": self.target_version,
            "quarter_id": self.quarter_id,
            "taxonomy_version": self.taxonomy_version,
            "created_at": self.created_at,
            "overall_target_score": self.overall_target_score,
            "near_threshold": self.near_threshold,
            "domain_targets": [
                {
                    "domain_id": target.domain_id,
                    "target_score": target.target_score,
                    "near_threshold": target.near_threshold,
                    "metadata": dict(target.metadata),
                }
                for target in sorted(self.domain_targets, key=lambda item: item.domain_id)
            ],
            "capability_targets": [
                {
                    "capability_id": target.capability_id,
                    "domain_id": target.domain_id,
                    "target_score": target.target_score,
                    "near_threshold": target.near_threshold,
                    "metadata": dict(target.metadata),
                }
                for target in sorted(
                    self.capability_targets,
                    key=lambda item: item.capability_id,
                )
            ],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class QuarterlyDomainGap:
    domain_id: str
    baseline_score: float
    latest_score: float
    target_score: float
    gap_to_target: float
    delta_since_baseline: float
    status: GapStatus
    priority_score: float
    capability_count: int


@dataclass(frozen=True)
class QuarterlyCapabilityGap:
    capability_id: str
    domain_id: str
    baseline_score: float
    latest_score: float
    target_score: float
    gap_to_target: float
    delta_since_baseline: float
    status: GapStatus
    priority_score: float


@dataclass(frozen=True)
class QuarterlyGapRecommendation:
    recommendation_id: str
    label: str
    rationale: str
    priority_score: float
    source: str
    related_root_causes: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class QuarterlyGapReport:
    report_id: str
    generated_at: str
    quarter_id: str
    taxonomy_version: str
    scoring_version: str
    run_count: int
    window_size: int
    baseline_run_id: str
    latest_run_id: str
    overall_baseline_score: float
    overall_latest_score: float
    overall_target_score: float
    overall_gap_to_target: float
    overall_delta: float
    overall_status: GapStatus
    risk_level: GapRiskLevel
    domain_gaps: tuple[QuarterlyDomainGap, ...]
    capability_gaps: tuple[QuarterlyCapabilityGap, ...]
    recommendations: tuple[QuarterlyGapRecommendation, ...]
    markdown: str
    deterministic_digest: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "quarter_id": self.quarter_id,
            "taxonomy_version": self.taxonomy_version,
            "scoring_version": self.scoring_version,
            "run_count": self.run_count,
            "window_size": self.window_size,
            "baseline_run_id": self.baseline_run_id,
            "latest_run_id": self.latest_run_id,
            "overall_baseline_score": self.overall_baseline_score,
            "overall_latest_score": self.overall_latest_score,
            "overall_target_score": self.overall_target_score,
            "overall_gap_to_target": self.overall_gap_to_target,
            "overall_delta": self.overall_delta,
            "overall_status": self.overall_status,
            "risk_level": self.risk_level,
            "domain_gaps": [
                {
                    "domain_id": gap.domain_id,
                    "baseline_score": gap.baseline_score,
                    "latest_score": gap.latest_score,
                    "target_score": gap.target_score,
                    "gap_to_target": gap.gap_to_target,
                    "delta_since_baseline": gap.delta_since_baseline,
                    "status": gap.status,
                    "priority_score": gap.priority_score,
                    "capability_count": gap.capability_count,
                }
                for gap in sorted(self.domain_gaps, key=lambda item: item.domain_id)
            ],
            "capability_gaps": [
                {
                    "capability_id": gap.capability_id,
                    "domain_id": gap.domain_id,
                    "baseline_score": gap.baseline_score,
                    "latest_score": gap.latest_score,
                    "target_score": gap.target_score,
                    "gap_to_target": gap.gap_to_target,
                    "delta_since_baseline": gap.delta_since_baseline,
                    "status": gap.status,
                    "priority_score": gap.priority_score,
                }
                for gap in sorted(self.capability_gaps, key=lambda item: item.capability_id)
            ],
            "recommendations": [
                {
                    "recommendation_id": recommendation.recommendation_id,
                    "label": recommendation.label,
                    "rationale": recommendation.rationale,
                    "priority_score": recommendation.priority_score,
                    "source": recommendation.source,
                    "related_root_causes": list(recommendation.related_root_causes),
                    "metadata": dict(recommendation.metadata),
                }
                for recommendation in sorted(
                    self.recommendations,
                    key=lambda item: item.recommendation_id,
                )
            ],
            "markdown": self.markdown,
            "deterministic_digest": self.deterministic_digest,
            "metadata": dict(self.metadata),
        }


class QuarterlyGapReportError(ValueError):
    """Raised when quarterly gap-report inputs are invalid."""


class QuarterlyGapReportGenerator:
    """Generates quarterly moonshot capability gap reports from benchmark runs."""

    def __init__(
        self,
        *,
        window_size: int = 8,
        max_recommendations: int = 8,
    ) -> None:
        if not isinstance(window_size, int):
            raise TypeError("window_size must be an integer")
        if window_size < 1:
            raise QuarterlyGapReportError("window_size must be at least 1")

        if not isinstance(max_recommendations, int):
            raise TypeError("max_recommendations must be an integer")
        if max_recommendations < 1:
            raise QuarterlyGapReportError("max_recommendations must be at least 1")

        self.window_size = window_size
        self.max_recommendations = max_recommendations

    def generate_report(
        self,
        runs: list[BenchmarkHarnessRunResult] | tuple[BenchmarkHarnessRunResult, ...],
        *,
        quarter_id: str | None = None,
        targets: MoonshotTargetProfile | None = None,
        failure_report: FailureLabelingReport | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> QuarterlyGapReport:
        normalized_runs = self._normalize_runs(runs)
        windowed_runs = normalized_runs[-self.window_size :]

        baseline_run = windowed_runs[0]
        latest_run = windowed_runs[-1]

        normalized_quarter_id = (
            _normalize_quarter_id(quarter_id)
            if quarter_id is not None
            else _infer_quarter_id(latest_run.completed_at)
        )

        if targets is None:
            targets = build_default_moonshot_target_profile(
                latest_run,
                quarter_id=normalized_quarter_id,
            )
        validate_moonshot_target_profile(targets, reference_run=latest_run)

        if targets.quarter_id != normalized_quarter_id:
            raise QuarterlyGapReportError(
                "quarter_id does not match target profile quarter"
            )

        if not isinstance(failure_report, (FailureLabelingReport, type(None))):
            raise TypeError("failure_report must be FailureLabelingReport or None")

        domain_targets = {
            target.domain_id: target
            for target in targets.domain_targets
        }
        capability_targets = {
            target.capability_id: target
            for target in targets.capability_targets
        }

        domain_gaps: list[QuarterlyDomainGap] = []
        capability_gaps: list[QuarterlyCapabilityGap] = []

        for latest_domain in sorted(latest_run.domain_scores, key=lambda item: item.domain_id):
            baseline_domain = _get_domain_score(baseline_run, latest_domain.domain_id)
            target_domain = domain_targets[latest_domain.domain_id]

            latest_score = round(float(latest_domain.weighted_score), 12)
            baseline_score = round(float(baseline_domain.weighted_score), 12)
            target_score = round(float(target_domain.target_score), 12)
            gap_to_target = round(latest_score - target_score, 12)
            delta = round(latest_score - baseline_score, 12)
            status = _derive_status(gap_to_target, target_domain.near_threshold)
            priority_score = round(
                max(0.0, target_score - latest_score) * float(latest_domain.weight),
                12,
            )

            domain_gaps.append(
                QuarterlyDomainGap(
                    domain_id=latest_domain.domain_id,
                    baseline_score=baseline_score,
                    latest_score=latest_score,
                    target_score=target_score,
                    gap_to_target=gap_to_target,
                    delta_since_baseline=delta,
                    status=status,
                    priority_score=priority_score,
                    capability_count=len(latest_domain.capability_scores),
                )
            )

            for latest_capability in sorted(
                latest_domain.capability_scores,
                key=lambda item: item.capability_id,
            ):
                baseline_capability = _get_capability_score(
                    baseline_run,
                    latest_domain.domain_id,
                    latest_capability.capability_id,
                )
                target_capability = capability_targets[latest_capability.capability_id]

                latest_capability_score = round(float(latest_capability.weighted_score), 12)
                baseline_capability_score = round(float(baseline_capability.weighted_score), 12)
                capability_target_score = round(float(target_capability.target_score), 12)
                capability_gap_to_target = round(
                    latest_capability_score - capability_target_score,
                    12,
                )
                capability_delta = round(
                    latest_capability_score - baseline_capability_score,
                    12,
                )
                capability_status = _derive_status(
                    capability_gap_to_target,
                    target_capability.near_threshold,
                )
                capability_priority_score = round(
                    max(0.0, capability_target_score - latest_capability_score)
                    * float(latest_capability.weight)
                    * float(latest_domain.weight),
                    12,
                )

                capability_gaps.append(
                    QuarterlyCapabilityGap(
                        capability_id=latest_capability.capability_id,
                        domain_id=latest_domain.domain_id,
                        baseline_score=baseline_capability_score,
                        latest_score=latest_capability_score,
                        target_score=capability_target_score,
                        gap_to_target=capability_gap_to_target,
                        delta_since_baseline=capability_delta,
                        status=capability_status,
                        priority_score=capability_priority_score,
                    )
                )

        overall_baseline_score = round(float(baseline_run.overall_score), 12)
        overall_latest_score = round(float(latest_run.overall_score), 12)
        overall_target_score = round(float(targets.overall_target_score), 12)
        overall_gap_to_target = round(overall_latest_score - overall_target_score, 12)
        overall_delta = round(overall_latest_score - overall_baseline_score, 12)
        overall_status = _derive_status(overall_gap_to_target, targets.near_threshold)

        recommendations = _build_recommendations(
            domain_gaps=domain_gaps,
            capability_gaps=capability_gaps,
            failure_report=failure_report,
            max_recommendations=self.max_recommendations,
        )
        risk_level = _derive_risk_level(
            overall_status=overall_status,
            recommendations=recommendations,
        )

        deterministic_digest = _build_report_digest(
            quarter_id=targets.quarter_id,
            runs=windowed_runs,
            targets=targets,
            overall_status=overall_status,
            domain_gaps=domain_gaps,
            capability_gaps=capability_gaps,
            recommendations=recommendations,
        )
        report_id = f"gap-report-{deterministic_digest[:24]}"

        markdown = _render_markdown(
            quarter_id=targets.quarter_id,
            baseline_run_id=baseline_run.run_id,
            latest_run_id=latest_run.run_id,
            overall_baseline_score=overall_baseline_score,
            overall_latest_score=overall_latest_score,
            overall_target_score=overall_target_score,
            overall_gap_to_target=overall_gap_to_target,
            overall_delta=overall_delta,
            overall_status=overall_status,
            risk_level=risk_level,
            domain_gaps=domain_gaps,
            capability_gaps=capability_gaps,
            recommendations=recommendations,
        )

        return QuarterlyGapReport(
            report_id=report_id,
            generated_at=_utc_now_iso(),
            quarter_id=targets.quarter_id,
            taxonomy_version=latest_run.taxonomy_version,
            scoring_version=latest_run.scoring_version,
            run_count=len(windowed_runs),
            window_size=self.window_size,
            baseline_run_id=baseline_run.run_id,
            latest_run_id=latest_run.run_id,
            overall_baseline_score=overall_baseline_score,
            overall_latest_score=overall_latest_score,
            overall_target_score=overall_target_score,
            overall_gap_to_target=overall_gap_to_target,
            overall_delta=overall_delta,
            overall_status=overall_status,
            risk_level=risk_level,
            domain_gaps=tuple(sorted(domain_gaps, key=lambda item: item.domain_id)),
            capability_gaps=tuple(sorted(capability_gaps, key=lambda item: item.capability_id)),
            recommendations=tuple(
                sorted(
                    recommendations,
                    key=lambda item: (-item.priority_score, item.recommendation_id),
                )
            ),
            markdown=markdown,
            deterministic_digest=deterministic_digest,
            metadata=dict(metadata or {}),
        )

    def _normalize_runs(
        self,
        runs: list[BenchmarkHarnessRunResult] | tuple[BenchmarkHarnessRunResult, ...],
    ) -> tuple[BenchmarkHarnessRunResult, ...]:
        if not runs:
            raise QuarterlyGapReportError("runs must include at least one benchmark result")

        normalized_runs: list[BenchmarkHarnessRunResult] = []
        for run in runs:
            if not isinstance(run, BenchmarkHarnessRunResult):
                raise TypeError("runs must contain BenchmarkHarnessRunResult entries")
            if not run.strict_coverage:
                raise QuarterlyGapReportError("runs must enable strict_coverage")
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
                raise QuarterlyGapReportError("All runs must share taxonomy_version")
            if run.scoring_version != reference.scoring_version:
                raise QuarterlyGapReportError("All runs must share scoring_version")

            domain_ids = tuple(sorted(domain.domain_id for domain in run.domain_scores))
            if domain_ids != reference_domain_ids:
                raise QuarterlyGapReportError("All runs must share domain coverage")

            capability_ids = tuple(
                sorted(
                    capability.capability_id
                    for domain in run.domain_scores
                    for capability in domain.capability_scores
                )
            )
            if capability_ids != reference_capability_ids:
                raise QuarterlyGapReportError("All runs must share capability coverage")

        return tuple(normalized_runs)


def build_default_moonshot_target_profile(
    run: BenchmarkHarnessRunResult,
    *,
    quarter_id: str | None = None,
    target_version: str = "1.0.0",
    near_threshold: float = 0.02,
    metadata: dict[str, Any] | None = None,
) -> MoonshotTargetProfile:
    if not isinstance(run, BenchmarkHarnessRunResult):
        raise TypeError("run must be BenchmarkHarnessRunResult")

    normalized_target_version = _normalize_required(target_version, "target_version")
    normalized_quarter_id = (
        _normalize_quarter_id(quarter_id)
        if quarter_id is not None
        else _infer_quarter_id(run.completed_at)
    )
    normalized_near_threshold = _normalize_threshold(near_threshold, "near_threshold")

    default_domain_targets = {
        "reasoning": 0.84,
        "planning": 0.84,
        "memory": 0.80,
        "tool_use": 0.80,
    }

    domain_targets: list[MoonshotDomainTarget] = []
    capability_targets: list[MoonshotCapabilityTarget] = []

    for domain in sorted(run.domain_scores, key=lambda item: item.domain_id):
        domain_target_score = default_domain_targets.get(domain.domain_id, 0.80)
        domain_targets.append(
            MoonshotDomainTarget(
                domain_id=domain.domain_id,
                target_score=round(float(domain_target_score), 12),
                near_threshold=normalized_near_threshold,
                metadata={"default_target": True},
            )
        )

        for capability in sorted(domain.capability_scores, key=lambda item: item.capability_id):
            capability_target_score = min(0.95, domain_target_score + 0.01)
            capability_targets.append(
                MoonshotCapabilityTarget(
                    capability_id=capability.capability_id,
                    domain_id=domain.domain_id,
                    target_score=round(float(capability_target_score), 12),
                    near_threshold=normalized_near_threshold,
                    metadata={"default_target": True},
                )
            )

    weight_denominator = sum(float(domain.weight) for domain in run.domain_scores)
    if weight_denominator <= 0:
        raise QuarterlyGapReportError("run domain weight denominator must be positive")

    domain_target_by_id = {
        target.domain_id: target.target_score
        for target in domain_targets
    }
    overall_target_score = round(
        sum(
            float(domain.weight) * domain_target_by_id[domain.domain_id]
            for domain in run.domain_scores
        )
        / weight_denominator,
        12,
    )

    target_id = _build_target_id(
        quarter_id=normalized_quarter_id,
        target_version=normalized_target_version,
        taxonomy_version=run.taxonomy_version,
        domain_targets=domain_targets,
        capability_targets=capability_targets,
    )

    profile = MoonshotTargetProfile(
        target_id=target_id,
        target_version=normalized_target_version,
        quarter_id=normalized_quarter_id,
        taxonomy_version=run.taxonomy_version,
        created_at=_utc_now_iso(),
        overall_target_score=overall_target_score,
        near_threshold=normalized_near_threshold,
        domain_targets=tuple(sorted(domain_targets, key=lambda item: item.domain_id)),
        capability_targets=tuple(
            sorted(capability_targets, key=lambda item: item.capability_id)
        ),
        metadata=dict(metadata or {}),
    )
    validate_moonshot_target_profile(profile, reference_run=run)
    return profile


def validate_moonshot_target_profile(
    profile: MoonshotTargetProfile,
    *,
    reference_run: BenchmarkHarnessRunResult | None = None,
) -> None:
    if not isinstance(profile, MoonshotTargetProfile):
        raise TypeError("profile must be MoonshotTargetProfile")

    _normalize_required(profile.target_id, "target_id")
    _normalize_required(profile.target_version, "target_version")
    _normalize_quarter_id(profile.quarter_id)
    _normalize_required(profile.taxonomy_version, "taxonomy_version")
    _parse_iso(profile.created_at)
    _normalize_score(profile.overall_target_score, "overall_target_score")
    _normalize_threshold(profile.near_threshold, "near_threshold")

    if not profile.domain_targets:
        raise QuarterlyGapReportError("profile must include domain_targets")
    if not profile.capability_targets:
        raise QuarterlyGapReportError("profile must include capability_targets")

    domain_targets_by_id: dict[str, MoonshotDomainTarget] = {}
    for target in profile.domain_targets:
        domain_id = _normalize_required(target.domain_id, "domain_id").lower()
        if domain_id in domain_targets_by_id:
            raise QuarterlyGapReportError(f"Duplicate domain target: {domain_id}")
        _normalize_score(target.target_score, f"{domain_id}.target_score")
        _normalize_threshold(target.near_threshold, f"{domain_id}.near_threshold")
        domain_targets_by_id[domain_id] = target

    capability_targets_by_id: dict[str, MoonshotCapabilityTarget] = {}
    domain_to_capabilities: dict[str, list[str]] = {}
    for target in profile.capability_targets:
        capability_id = _normalize_required(
            target.capability_id,
            "capability_id",
        ).lower()
        if capability_id in capability_targets_by_id:
            raise QuarterlyGapReportError(
                f"Duplicate capability target: {capability_id}"
            )

        domain_id = _normalize_required(target.domain_id, "capability.domain_id").lower()
        if domain_id not in domain_targets_by_id:
            raise QuarterlyGapReportError(
                f"Capability target {capability_id} references unknown domain {domain_id}"
            )

        _normalize_score(target.target_score, f"{capability_id}.target_score")
        _normalize_threshold(
            target.near_threshold,
            f"{capability_id}.near_threshold",
        )

        capability_targets_by_id[capability_id] = target
        domain_to_capabilities.setdefault(domain_id, []).append(capability_id)

    for domain_id in domain_targets_by_id:
        if domain_id not in domain_to_capabilities:
            raise QuarterlyGapReportError(
                f"Domain target {domain_id} must include at least one capability target"
            )

    if reference_run is not None:
        if not isinstance(reference_run, BenchmarkHarnessRunResult):
            raise TypeError("reference_run must be BenchmarkHarnessRunResult")
        if profile.taxonomy_version != reference_run.taxonomy_version:
            raise QuarterlyGapReportError(
                "profile taxonomy_version must match reference run"
            )

        run_domain_ids = tuple(
            sorted(domain.domain_id for domain in reference_run.domain_scores)
        )
        profile_domain_ids = tuple(sorted(domain_targets_by_id))
        if run_domain_ids != profile_domain_ids:
            raise QuarterlyGapReportError(
                "profile domain target coverage must match reference run"
            )

        run_capability_ids = tuple(
            sorted(
                capability.capability_id
                for domain in reference_run.domain_scores
                for capability in domain.capability_scores
            )
        )
        profile_capability_ids = tuple(sorted(capability_targets_by_id))
        if run_capability_ids != profile_capability_ids:
            raise QuarterlyGapReportError(
                "profile capability target coverage must match reference run"
            )

        expected_capability_domains = {
            capability.capability_id: domain.domain_id
            for domain in reference_run.domain_scores
            for capability in domain.capability_scores
        }
        for capability_id, capability_target in capability_targets_by_id.items():
            expected_domain_id = expected_capability_domains[capability_id]
            if capability_target.domain_id != expected_domain_id:
                raise QuarterlyGapReportError(
                    f"Capability target {capability_id} domain mismatch: expected {expected_domain_id}, got {capability_target.domain_id}"
                )


def _get_domain_score(run: BenchmarkHarnessRunResult, domain_id: str):
    for domain in run.domain_scores:
        if domain.domain_id == domain_id:
            return domain
    raise QuarterlyGapReportError(f"Run {run.run_id} is missing domain {domain_id}")


def _get_capability_score(
    run: BenchmarkHarnessRunResult,
    domain_id: str,
    capability_id: str,
):
    domain = _get_domain_score(run, domain_id)
    for capability in domain.capability_scores:
        if capability.capability_id == capability_id:
            return capability
    raise QuarterlyGapReportError(
        f"Run {run.run_id} domain {domain_id} missing capability {capability_id}"
    )


def _derive_status(gap_to_target: float, near_threshold: float) -> GapStatus:
    if gap_to_target >= 0.0:
        return "met"
    if gap_to_target >= -near_threshold:
        return "near"
    return "off_track"


def _build_recommendations(
    *,
    domain_gaps: list[QuarterlyDomainGap],
    capability_gaps: list[QuarterlyCapabilityGap],
    failure_report: FailureLabelingReport | None,
    max_recommendations: int,
) -> tuple[QuarterlyGapRecommendation, ...]:
    recommendations: list[QuarterlyGapRecommendation] = []

    if failure_report is not None:
        for label in sorted(
            failure_report.labels,
            key=lambda item: (-item.confidence, item.root_cause_id),
        ):
            recommendation = _recommendation_from_failure_label(label)
            recommendations.append(recommendation)

    prioritized_capability_gaps = sorted(
        [gap for gap in capability_gaps if gap.status != "met"],
        key=lambda item: (-item.priority_score, item.capability_id),
    )
    for gap in prioritized_capability_gaps[:4]:
        recommendations.append(
            QuarterlyGapRecommendation(
                recommendation_id=_build_recommendation_id(
                    source="gap_analysis",
                    key=f"capability:{gap.capability_id}",
                ),
                label=_default_capability_focus(gap.domain_id, gap.capability_id),
                rationale=(
                    f"Capability {gap.capability_id} is {gap.status} with target gap {gap.gap_to_target:+.4f}."
                ),
                priority_score=round(gap.priority_score + 0.20, 12),
                source="gap_analysis",
                related_root_causes=(),
                metadata={
                    "domain_id": gap.domain_id,
                    "capability_id": gap.capability_id,
                    "status": gap.status,
                },
            )
        )

    prioritized_domain_gaps = sorted(
        [gap for gap in domain_gaps if gap.status == "off_track"],
        key=lambda item: (-item.priority_score, item.domain_id),
    )
    for gap in prioritized_domain_gaps[:2]:
        recommendations.append(
            QuarterlyGapRecommendation(
                recommendation_id=_build_recommendation_id(
                    source="gap_analysis",
                    key=f"domain:{gap.domain_id}",
                ),
                label=_default_domain_focus(gap.domain_id),
                rationale=(
                    f"Domain {gap.domain_id} is off-track with target gap {gap.gap_to_target:+.4f}."
                ),
                priority_score=round(gap.priority_score + 0.15, 12),
                source="gap_analysis",
                related_root_causes=(),
                metadata={
                    "domain_id": gap.domain_id,
                    "status": gap.status,
                },
            )
        )

    deduplicated: dict[str, QuarterlyGapRecommendation] = {}
    for recommendation in recommendations:
        existing = deduplicated.get(recommendation.label)
        if existing is None or recommendation.priority_score > existing.priority_score:
            deduplicated[recommendation.label] = recommendation

    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (-item.priority_score, item.recommendation_id),
    )
    return tuple(ordered[:max_recommendations])


def _recommendation_from_failure_label(
    label: FailureRootCauseLabel,
) -> QuarterlyGapRecommendation:
    focus_label = label.remediation_labels[0] if label.remediation_labels else label.root_cause_id
    priority_score = round(label.confidence * _severity_weight(label.severity), 12)
    return QuarterlyGapRecommendation(
        recommendation_id=_build_recommendation_id(
            source="failure_taxonomy",
            key=f"{label.label_id}:{focus_label}",
        ),
        label=focus_label,
        rationale=(
            f"Root cause {label.root_cause_id} severity={label.severity} confidence={label.confidence:.2f}."
        ),
        priority_score=priority_score,
        source="failure_taxonomy",
        related_root_causes=(label.root_cause_id,),
        metadata={
            "severity": label.severity,
            "confidence": label.confidence,
            "category_id": label.category_id,
        },
    )


def _default_domain_focus(domain_id: str) -> str:
    mapping = {
        "reasoning": "tighten reasoning error analysis",
        "planning": "expand long-horizon replanning drills",
        "memory": "harden memory grounding checks",
        "tool_use": "stabilize tool orchestration reliability",
    }
    return mapping.get(domain_id, f"improve domain coverage for {domain_id}")


def _default_capability_focus(domain_id: str, capability_id: str) -> str:
    return f"raise {domain_id} capability {capability_id} toward target"


def _derive_risk_level(
    *,
    overall_status: GapStatus,
    recommendations: tuple[QuarterlyGapRecommendation, ...],
) -> GapRiskLevel:
    if overall_status == "off_track":
        return "elevated"

    severe_failure_signal = any(
        recommendation.source == "failure_taxonomy"
        and recommendation.metadata.get("severity") in {"high", "critical"}
        for recommendation in recommendations
    )
    if severe_failure_signal:
        return "elevated"

    has_failure_recommendation = any(
        recommendation.source == "failure_taxonomy"
        for recommendation in recommendations
    )
    if overall_status == "near" or has_failure_recommendation:
        return "moderate"
    return "low"


def _severity_weight(severity: str) -> float:
    normalized = _normalize_required(severity, "severity").lower()
    if normalized == "critical":
        return 1.8
    if normalized == "high":
        return 1.55
    if normalized == "medium":
        return 1.25
    return 1.0


def _build_target_id(
    *,
    quarter_id: str,
    target_version: str,
    taxonomy_version: str,
    domain_targets: list[MoonshotDomainTarget],
    capability_targets: list[MoonshotCapabilityTarget],
) -> str:
    canonical = json.dumps(
        {
            "quarter_id": quarter_id,
            "target_version": target_version,
            "taxonomy_version": taxonomy_version,
            "domain_targets": [
                {
                    "domain_id": target.domain_id,
                    "target_score": target.target_score,
                    "near_threshold": target.near_threshold,
                }
                for target in sorted(domain_targets, key=lambda item: item.domain_id)
            ],
            "capability_targets": [
                {
                    "capability_id": target.capability_id,
                    "domain_id": target.domain_id,
                    "target_score": target.target_score,
                    "near_threshold": target.near_threshold,
                }
                for target in sorted(capability_targets, key=lambda item: item.capability_id)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"target-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _build_report_digest(
    *,
    quarter_id: str,
    runs: tuple[BenchmarkHarnessRunResult, ...],
    targets: MoonshotTargetProfile,
    overall_status: GapStatus,
    domain_gaps: list[QuarterlyDomainGap],
    capability_gaps: list[QuarterlyCapabilityGap],
    recommendations: tuple[QuarterlyGapRecommendation, ...],
) -> str:
    canonical = json.dumps(
        {
            "quarter_id": quarter_id,
            "run_ids": [run.run_id for run in runs],
            "run_digests": [run.deterministic_digest for run in runs],
            "target_id": targets.target_id,
            "overall_status": overall_status,
            "domain_gaps": [
                {
                    "domain_id": gap.domain_id,
                    "gap_to_target": gap.gap_to_target,
                    "status": gap.status,
                }
                for gap in sorted(domain_gaps, key=lambda item: item.domain_id)
            ],
            "capability_gaps": [
                {
                    "capability_id": gap.capability_id,
                    "gap_to_target": gap.gap_to_target,
                    "status": gap.status,
                }
                for gap in sorted(capability_gaps, key=lambda item: item.capability_id)
            ],
            "recommendations": [
                {
                    "label": recommendation.label,
                    "source": recommendation.source,
                    "priority_score": recommendation.priority_score,
                }
                for recommendation in sorted(
                    recommendations,
                    key=lambda item: item.recommendation_id,
                )
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _build_recommendation_id(*, source: str, key: str) -> str:
    canonical = json.dumps(
        {"source": source, "key": key},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"rec-{sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _render_markdown(
    *,
    quarter_id: str,
    baseline_run_id: str,
    latest_run_id: str,
    overall_baseline_score: float,
    overall_latest_score: float,
    overall_target_score: float,
    overall_gap_to_target: float,
    overall_delta: float,
    overall_status: GapStatus,
    risk_level: GapRiskLevel,
    domain_gaps: list[QuarterlyDomainGap],
    capability_gaps: list[QuarterlyCapabilityGap],
    recommendations: tuple[QuarterlyGapRecommendation, ...],
) -> str:
    lines = [
        "# Quarterly Moonshot Gap Report",
        "",
        f"Quarter: {quarter_id}",
        f"Baseline run: {baseline_run_id}",
        f"Latest run: {latest_run_id}",
        f"Overall baseline score: {overall_baseline_score:.4f}",
        f"Overall latest score: {overall_latest_score:.4f}",
        f"Overall target score: {overall_target_score:.4f}",
        f"Overall target gap: {overall_gap_to_target:+.4f} ({overall_status})",
        f"Overall quarter delta: {overall_delta:+.4f}",
        f"Risk level: {risk_level}",
        "",
        "## Domain Gap Summary",
    ]

    for gap in sorted(domain_gaps, key=lambda item: item.domain_id):
        lines.append(
            (
                f"- {gap.domain_id}: latest={gap.latest_score:.4f}, "
                f"target={gap.target_score:.4f}, gap={gap.gap_to_target:+.4f}, "
                f"delta={gap.delta_since_baseline:+.4f}, status={gap.status}"
            )
        )

    lines.append("")
    lines.append("## Capability Priorities")
    prioritized = sorted(
        capability_gaps,
        key=lambda item: (-item.priority_score, item.capability_id),
    )[:8]
    for gap in prioritized:
        lines.append(
            (
                f"- {gap.capability_id} ({gap.domain_id}): latest={gap.latest_score:.4f}, "
                f"target={gap.target_score:.4f}, gap={gap.gap_to_target:+.4f}, status={gap.status}"
            )
        )

    lines.append("")
    lines.append("## Recommended Remediation Focus")
    if not recommendations:
        lines.append("- No remediation recommendations generated.")
    else:
        for recommendation in recommendations:
            lines.append(
                (
                    f"- {recommendation.label} ({recommendation.source}, priority={recommendation.priority_score:.3f}): "
                    f"{recommendation.rationale}"
                )
            )

    return "\n".join(lines)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise QuarterlyGapReportError(f"{field_name} is required")
    return normalized


def _normalize_score(value: float, field_name: str) -> float:
    try:
        normalized = float(value)
    except Exception as exc:
        raise QuarterlyGapReportError(f"{field_name} must be numeric") from exc
    if normalized < 0 or normalized > 1:
        raise QuarterlyGapReportError(f"{field_name} must be between 0 and 1")
    return normalized


def _normalize_threshold(value: float, field_name: str) -> float:
    try:
        normalized = float(value)
    except Exception as exc:
        raise QuarterlyGapReportError(f"{field_name} must be numeric") from exc
    if normalized < 0 or normalized > 0.25:
        raise QuarterlyGapReportError(f"{field_name} must be between 0 and 0.25")
    return round(normalized, 12)


def _normalize_quarter_id(value: str) -> str:
    normalized = _normalize_required(value, "quarter_id").upper()
    parts = normalized.split("-")
    if len(parts) != 2:
        raise QuarterlyGapReportError(
            "quarter_id must be in format YYYY-QN"
        )

    year, quarter_token = parts
    if len(year) != 4 or not year.isdigit():
        raise QuarterlyGapReportError("quarter_id year must be a four-digit number")
    if len(quarter_token) != 2 or not quarter_token.startswith("Q"):
        raise QuarterlyGapReportError("quarter_id quarter token must be Q1..Q4")

    quarter_index = quarter_token[1]
    if quarter_index not in {"1", "2", "3", "4"}:
        raise QuarterlyGapReportError("quarter_id quarter token must be Q1..Q4")
    return f"{year}-Q{quarter_index}"


def _infer_quarter_id(timestamp: str) -> str:
    parsed = _parse_iso(timestamp)
    quarter = ((parsed.month - 1) // 3) + 1
    return f"{parsed.year}-Q{quarter}"


def _parse_iso(value: str) -> datetime:
    normalized = _normalize_required(value, "timestamp")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "MoonshotDomainTarget",
    "MoonshotCapabilityTarget",
    "MoonshotTargetProfile",
    "QuarterlyDomainGap",
    "QuarterlyCapabilityGap",
    "QuarterlyGapRecommendation",
    "QuarterlyGapReport",
    "QuarterlyGapReportError",
    "QuarterlyGapReportGenerator",
    "build_default_moonshot_target_profile",
    "validate_moonshot_target_profile",
]