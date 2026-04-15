"""Failure taxonomy and root-cause labeling for moonshot benchmark operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from .safety_regression_gate import SafetyRegressionGateResult

Severity = Literal["low", "medium", "high", "critical"]

_ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}


@dataclass(frozen=True)
class FailureCategoryDefinition:
    category_id: str
    title: str
    description: str
    root_cause_ids: tuple[str, ...]
    severity_weight: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FailureRootCauseDefinition:
    root_cause_id: str
    category_id: str
    title: str
    description: str
    keywords: tuple[str, ...]
    remediation_labels: tuple[str, ...]
    default_severity: str
    weight: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FailureTaxonomy:
    taxonomy_version: str
    created_at: str
    categories: tuple[FailureCategoryDefinition, ...]
    root_causes: tuple[FailureRootCauseDefinition, ...]
    metadata: dict[str, Any]

    def get_category(self, category_id: str) -> FailureCategoryDefinition:
        normalized_category_id = _normalize_required(category_id, "category_id").lower()
        for category in self.categories:
            if category.category_id == normalized_category_id:
                return category
        raise KeyError(f"Unknown failure category: {normalized_category_id}")

    def get_root_cause(self, root_cause_id: str) -> FailureRootCauseDefinition:
        normalized_root_cause_id = _normalize_required(root_cause_id, "root_cause_id").lower()
        for root_cause in self.root_causes:
            if root_cause.root_cause_id == normalized_root_cause_id:
                return root_cause
        raise KeyError(f"Unknown failure root cause: {normalized_root_cause_id}")

    def to_manifest(self) -> dict[str, Any]:
        return {
            "taxonomy_version": self.taxonomy_version,
            "created_at": self.created_at,
            "categories": [
                {
                    "category_id": category.category_id,
                    "title": category.title,
                    "description": category.description,
                    "root_cause_ids": list(category.root_cause_ids),
                    "severity_weight": category.severity_weight,
                    "metadata": dict(category.metadata),
                }
                for category in sorted(self.categories, key=lambda item: item.category_id)
            ],
            "root_causes": [
                {
                    "root_cause_id": root_cause.root_cause_id,
                    "category_id": root_cause.category_id,
                    "title": root_cause.title,
                    "description": root_cause.description,
                    "keywords": list(root_cause.keywords),
                    "remediation_labels": list(root_cause.remediation_labels),
                    "default_severity": root_cause.default_severity,
                    "weight": root_cause.weight,
                    "metadata": dict(root_cause.metadata),
                }
                for root_cause in sorted(self.root_causes, key=lambda item: item.root_cause_id)
            ],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class FailureSignal:
    signal_id: str
    source_id: str
    metric_id: str
    severity: str
    description: str
    observed_value: float | None
    expected_value: float | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FailureRootCauseLabel:
    label_id: str
    category_id: str
    root_cause_id: str
    severity: str
    confidence: float
    evidence_signal_ids: tuple[str, ...]
    rationale: str
    remediation_labels: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FailureLabelingReport:
    report_id: str
    generated_at: str
    taxonomy_version: str
    signal_count: int
    labels: tuple[FailureRootCauseLabel, ...]
    unmatched_signal_ids: tuple[str, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "taxonomy_version": self.taxonomy_version,
            "signal_count": self.signal_count,
            "labels": [
                {
                    "label_id": label.label_id,
                    "category_id": label.category_id,
                    "root_cause_id": label.root_cause_id,
                    "severity": label.severity,
                    "confidence": label.confidence,
                    "evidence_signal_ids": list(label.evidence_signal_ids),
                    "rationale": label.rationale,
                    "remediation_labels": list(label.remediation_labels),
                    "metadata": dict(label.metadata),
                }
                for label in sorted(self.labels, key=lambda item: item.label_id)
            ],
            "unmatched_signal_ids": list(self.unmatched_signal_ids),
            "metadata": dict(self.metadata),
        }


class FailureTaxonomyError(ValueError):
    """Raised when failure taxonomy or root-cause labeling inputs are invalid."""


class FailureRootCauseLabeler:
    """Applies taxonomy-driven labeling to failure signals and safety-gate outcomes."""

    def __init__(
        self,
        taxonomy: FailureTaxonomy | None = None,
    ) -> None:
        if taxonomy is None:
            taxonomy = build_default_failure_taxonomy()
        validate_failure_taxonomy(taxonomy)
        self.taxonomy = taxonomy

    def label_signals(
        self,
        signals: list[FailureSignal] | tuple[FailureSignal, ...],
        *,
        max_labels: int = 4,
        min_confidence: float = 0.2,
        metadata: dict[str, Any] | None = None,
    ) -> FailureLabelingReport:
        normalized_signals = _normalize_signals(signals)

        if not isinstance(max_labels, int):
            raise TypeError("max_labels must be an integer")
        if max_labels < 1:
            raise FailureTaxonomyError("max_labels must be at least 1")

        try:
            normalized_min_confidence = float(min_confidence)
        except Exception as exc:
            raise FailureTaxonomyError("min_confidence must be numeric") from exc
        if normalized_min_confidence < 0 or normalized_min_confidence > 1:
            raise FailureTaxonomyError("min_confidence must be between 0 and 1")

        scored_labels: list[FailureRootCauseLabel] = []
        matched_signal_ids: set[str] = set()

        categories_by_id = {category.category_id: category for category in self.taxonomy.categories}

        for root_cause in sorted(self.taxonomy.root_causes, key=lambda item: item.root_cause_id):
            category = categories_by_id[root_cause.category_id]
            score = 0.0
            root_keywords: set[str] = set(root_cause.keywords)
            evidence_signal_ids: list[str] = []
            keyword_hits_by_signal: dict[str, list[str]] = {}

            for signal in normalized_signals:
                signal_text = _build_signal_text(signal)
                matched_keywords = sorted(
                    keyword
                    for keyword in root_keywords
                    if keyword in signal_text
                )
                if not matched_keywords:
                    continue

                severity_factor = _severity_factor(signal.severity)
                hit_factor = 1.0 + (0.15 * max(0, len(matched_keywords) - 1))
                score += root_cause.weight * category.severity_weight * severity_factor * hit_factor
                evidence_signal_ids.append(signal.signal_id)
                keyword_hits_by_signal[signal.signal_id] = matched_keywords

            if not evidence_signal_ids:
                continue

            confidence = round(min(0.99, score / (score + 3.0)), 12)
            if confidence < normalized_min_confidence:
                continue

            matched_signal_ids.update(evidence_signal_ids)
            label_severity = _derive_label_severity(
                default_severity=root_cause.default_severity,
                signals=[
                    signal
                    for signal in normalized_signals
                    if signal.signal_id in evidence_signal_ids
                ],
            )
            evidence_signal_ids_tuple = tuple(sorted(set(evidence_signal_ids)))
            rationale = _build_rationale(root_cause.root_cause_id, keyword_hits_by_signal)

            label_id = _build_label_id(
                root_cause_id=root_cause.root_cause_id,
                signal_ids=evidence_signal_ids_tuple,
            )
            scored_labels.append(
                FailureRootCauseLabel(
                    label_id=label_id,
                    category_id=root_cause.category_id,
                    root_cause_id=root_cause.root_cause_id,
                    severity=label_severity,
                    confidence=confidence,
                    evidence_signal_ids=evidence_signal_ids_tuple,
                    rationale=rationale,
                    remediation_labels=root_cause.remediation_labels,
                    metadata={
                        "root_weight": root_cause.weight,
                        "category_weight": category.severity_weight,
                        "signal_matches": {
                            signal_id: keyword_hits_by_signal[signal_id]
                            for signal_id in evidence_signal_ids_tuple
                            if signal_id in keyword_hits_by_signal
                        },
                    },
                )
            )

        sorted_labels = sorted(
            scored_labels,
            key=lambda item: (-item.confidence, item.root_cause_id),
        )[:max_labels]

        selected_signal_ids = {
            signal_id
            for label in sorted_labels
            for signal_id in label.evidence_signal_ids
        }
        unmatched_signal_ids = tuple(
            signal.signal_id
            for signal in normalized_signals
            if signal.signal_id not in selected_signal_ids
        )

        report_id = _build_report_id(
            taxonomy_version=self.taxonomy.taxonomy_version,
            signal_ids=tuple(signal.signal_id for signal in normalized_signals),
            label_ids=tuple(label.label_id for label in sorted_labels),
        )

        return FailureLabelingReport(
            report_id=report_id,
            generated_at=_utc_now_iso(),
            taxonomy_version=self.taxonomy.taxonomy_version,
            signal_count=len(normalized_signals),
            labels=tuple(sorted_labels),
            unmatched_signal_ids=unmatched_signal_ids,
            metadata=dict(metadata or {}),
        )

    def label_safety_regression_result(
        self,
        gate_result: SafetyRegressionGateResult,
        *,
        max_labels: int = 4,
        min_confidence: float = 0.2,
        metadata: dict[str, Any] | None = None,
    ) -> FailureLabelingReport:
        if not isinstance(gate_result, SafetyRegressionGateResult):
            raise TypeError("gate_result must be SafetyRegressionGateResult")

        signals: list[FailureSignal] = []
        for index, violation in enumerate(gate_result.violations, start=1):
            signals.append(
                FailureSignal(
                    signal_id=f"gate-{index:02d}-{violation.reference_id}",
                    source_id=f"safety_regression_{violation.level}",
                    metric_id=violation.reference_id,
                    severity=_violation_severity(violation.level),
                    description=violation.message,
                    observed_value=violation.candidate_score,
                    expected_value=violation.baseline_score,
                    metadata={
                        "threshold": violation.threshold,
                        "score_drop": violation.score_drop,
                        **dict(violation.metadata),
                    },
                )
            )

        report_metadata = {
            "gate_id": gate_result.gate_id,
            "change_id": gate_result.change_id,
            "change_type": gate_result.change_type,
            "risk_tier": gate_result.risk_tier,
            "gate_decision": gate_result.decision,
        }
        if metadata:
            report_metadata.update(dict(metadata))

        return self.label_signals(
            signals,
            max_labels=max_labels,
            min_confidence=min_confidence,
            metadata=report_metadata,
        )


def build_default_failure_taxonomy() -> FailureTaxonomy:
    root_causes = (
        FailureRootCauseDefinition(
            root_cause_id="capability_score_regression",
            category_id="regression_quality",
            title="Capability Score Regression",
            description="Capability-level benchmark performance dropped below tolerated trend bounds.",
            keywords=("regression", "score", "delta", "declining", "capability", "domain", "overall"),
            remediation_labels=("retrain_candidate", "tighten_eval_suite", "recheck_feature_flags"),
            default_severity="high",
            weight=1.0,
            metadata={},
        ),
        FailureRootCauseDefinition(
            root_cause_id="benchmark_integrity_drift",
            category_id="regression_quality",
            title="Benchmark Integrity Drift",
            description="Benchmark comparison invalid due to inconsistent setup or coverage.",
            keywords=("integrity", "taxonomy_version", "scoring_version", "strict_coverage", "missing", "scenario_count"),
            remediation_labels=("align_benchmark_config", "restore_coverage", "rerun_baseline"),
            default_severity="critical",
            weight=1.1,
            metadata={},
        ),
        FailureRootCauseDefinition(
            root_cause_id="safety_policy_misconfiguration",
            category_id="safety_controls",
            title="Safety Policy Misconfiguration",
            description="Safety policy thresholds or rules were misconfigured for the change context.",
            keywords=("policy", "risk_tier", "threshold", "guardrail", "block", "deny"),
            remediation_labels=("audit_policy_matrix", "tighten_controls", "add_policy_tests"),
            default_severity="high",
            weight=0.95,
            metadata={},
        ),
        FailureRootCauseDefinition(
            root_cause_id="approval_workflow_breakdown",
            category_id="safety_controls",
            title="Approval Workflow Breakdown",
            description="Promotion flow violated approval, transition token, or rollback workflow requirements.",
            keywords=("approval", "transition_token", "rollback_token", "promotion", "rejected", "reviewer"),
            remediation_labels=("reenforce_approval_checks", "rotate_tokens", "manual_review_hold"),
            default_severity="high",
            weight=0.9,
            metadata={},
        ),
        FailureRootCauseDefinition(
            root_cause_id="memory_grounding_degradation",
            category_id="knowledge_grounding",
            title="Memory Grounding Degradation",
            description="Evidence grounding, recall, or citation consistency degraded.",
            keywords=("memory", "retrieval", "citation", "groundedness", "recall", "consistency"),
            remediation_labels=("refresh_memory_index", "tighten_citation_checks", "replay_context"),
            default_severity="medium",
            weight=0.8,
            metadata={},
        ),
        FailureRootCauseDefinition(
            root_cause_id="tool_orchestration_failure",
            category_id="execution_pathways",
            title="Tool Orchestration Failure",
            description="Tool selection, schema invocation, or orchestration path broke execution reliability.",
            keywords=("tool", "schema", "invocation", "workflow", "connector", "timeout", "orchestration"),
            remediation_labels=("harden_tool_routing", "repair_schema_contracts", "add_workflow_fallbacks"),
            default_severity="medium",
            weight=0.82,
            metadata={},
        ),
        FailureRootCauseDefinition(
            root_cause_id="experiment_design_gap",
            category_id="governance_process",
            title="Experiment Design Gap",
            description="Experiment hypothesis, coverage, or benchmark design was insufficient.",
            keywords=("experiment", "hypothesis", "coverage", "baseline", "variance", "design"),
            remediation_labels=("revise_experiment_design", "expand_hidden_set", "require_design_review"),
            default_severity="medium",
            weight=0.76,
            metadata={},
        ),
        FailureRootCauseDefinition(
            root_cause_id="operator_governance_gap",
            category_id="governance_process",
            title="Operator Governance Gap",
            description="Operational review, escalation, or governance handoff controls were missed.",
            keywords=("operator", "governance", "review", "escalation", "audit", "human_approval"),
            remediation_labels=("enforce_governance_gate", "expand_operator_drills", "audit_signoff"),
            default_severity="high",
            weight=0.88,
            metadata={},
        ),
    )

    categories = (
        FailureCategoryDefinition(
            category_id="regression_quality",
            title="Regression Quality",
            description="Failures driven by measurable capability regressions or benchmark integrity drift.",
            root_cause_ids=("capability_score_regression", "benchmark_integrity_drift"),
            severity_weight=1.15,
            metadata={},
        ),
        FailureCategoryDefinition(
            category_id="safety_controls",
            title="Safety Controls",
            description="Failures in policy, approval workflow, or rollback control pathways.",
            root_cause_ids=("safety_policy_misconfiguration", "approval_workflow_breakdown"),
            severity_weight=1.2,
            metadata={},
        ),
        FailureCategoryDefinition(
            category_id="knowledge_grounding",
            title="Knowledge Grounding",
            description="Failures tied to evidence grounding, memory continuity, and retrieval quality.",
            root_cause_ids=("memory_grounding_degradation",),
            severity_weight=0.9,
            metadata={},
        ),
        FailureCategoryDefinition(
            category_id="execution_pathways",
            title="Execution Pathways",
            description="Failures caused by tool invocation and multi-step execution instability.",
            root_cause_ids=("tool_orchestration_failure",),
            severity_weight=0.95,
            metadata={},
        ),
        FailureCategoryDefinition(
            category_id="governance_process",
            title="Governance Process",
            description="Failures in experiment process design and operator governance controls.",
            root_cause_ids=("experiment_design_gap", "operator_governance_gap"),
            severity_weight=1.0,
            metadata={},
        ),
    )

    taxonomy = FailureTaxonomy(
        taxonomy_version="1.0.0",
        created_at=_utc_now_iso(),
        categories=categories,
        root_causes=root_causes,
        metadata={
            "program": "moonshot_capability",
            "phase": "P10-T9",
            "notes": "Failure taxonomy and root-cause labels for benchmark and governance failures.",
        },
    )
    validate_failure_taxonomy(taxonomy)
    return taxonomy


def validate_failure_taxonomy(taxonomy: FailureTaxonomy) -> None:
    if not isinstance(taxonomy, FailureTaxonomy):
        raise TypeError("taxonomy must be FailureTaxonomy")

    _normalize_required(taxonomy.taxonomy_version, "taxonomy_version")
    _parse_iso_timestamp(taxonomy.created_at)

    if not taxonomy.categories:
        raise FailureTaxonomyError("taxonomy must include categories")
    if not taxonomy.root_causes:
        raise FailureTaxonomyError("taxonomy must include root_causes")

    categories_by_id: dict[str, FailureCategoryDefinition] = {}
    for category in taxonomy.categories:
        category_id = _normalize_required(category.category_id, "category_id").lower()
        if category_id in categories_by_id:
            raise FailureTaxonomyError(f"Duplicate category_id: {category_id}")

        if category.severity_weight <= 0:
            raise FailureTaxonomyError(f"Category {category_id} severity_weight must be positive")

        normalized_root_cause_ids = _normalize_identifier_tuple(
            category.root_cause_ids,
            field_name=f"{category_id}.root_cause_ids",
        )
        if not normalized_root_cause_ids:
            raise FailureTaxonomyError(f"Category {category_id} must include root_cause_ids")

        categories_by_id[category_id] = category

    root_causes_by_id: dict[str, FailureRootCauseDefinition] = {}
    for root_cause in taxonomy.root_causes:
        root_cause_id = _normalize_required(root_cause.root_cause_id, "root_cause_id").lower()
        if root_cause_id in root_causes_by_id:
            raise FailureTaxonomyError(f"Duplicate root_cause_id: {root_cause_id}")

        category_id = _normalize_required(root_cause.category_id, "root_cause.category_id").lower()
        if category_id not in categories_by_id:
            raise FailureTaxonomyError(
                f"Root cause {root_cause_id} references unknown category {category_id}"
            )

        normalized_keywords = _normalize_identifier_tuple(
            root_cause.keywords,
            field_name=f"{root_cause_id}.keywords",
        )
        if not normalized_keywords:
            raise FailureTaxonomyError(f"Root cause {root_cause_id} must include keywords")

        normalized_remediation_labels = _normalize_identifier_tuple(
            root_cause.remediation_labels,
            field_name=f"{root_cause_id}.remediation_labels",
        )
        if not normalized_remediation_labels:
            raise FailureTaxonomyError(
                f"Root cause {root_cause_id} must include remediation_labels"
            )

        default_severity = _normalize_required(root_cause.default_severity, "default_severity").lower()
        if default_severity not in _ALLOWED_SEVERITIES:
            allowed = ", ".join(sorted(_ALLOWED_SEVERITIES))
            raise FailureTaxonomyError(
                f"Root cause {root_cause_id} has unsupported default_severity {default_severity}. Allowed: {allowed}"
            )

        if root_cause.weight <= 0:
            raise FailureTaxonomyError(f"Root cause {root_cause_id} weight must be positive")

        root_causes_by_id[root_cause_id] = root_cause

    for category_id, category in categories_by_id.items():
        for root_cause_id in category.root_cause_ids:
            normalized_root_cause_id = _normalize_required(
                root_cause_id,
                f"{category_id}.root_cause_id",
            ).lower()
            root_cause = root_causes_by_id.get(normalized_root_cause_id)
            if root_cause is None:
                raise FailureTaxonomyError(
                    f"Category {category_id} references unknown root cause {normalized_root_cause_id}"
                )
            if root_cause.category_id != category_id:
                raise FailureTaxonomyError(
                    f"Root cause {normalized_root_cause_id} category mismatch: expected {category_id}, got {root_cause.category_id}"
                )


def _normalize_signals(
    signals: list[FailureSignal] | tuple[FailureSignal, ...],
) -> tuple[FailureSignal, ...]:
    if not signals:
        raise FailureTaxonomyError("signals must include at least one entry")

    normalized: list[FailureSignal] = []
    seen_ids: set[str] = set()
    for signal in signals:
        if not isinstance(signal, FailureSignal):
            raise TypeError("signals must contain FailureSignal entries")

        signal_id = _normalize_required(signal.signal_id, "signal_id").lower()
        if signal_id in seen_ids:
            raise FailureTaxonomyError(f"Duplicate signal_id: {signal_id}")
        seen_ids.add(signal_id)

        severity = _normalize_required(signal.severity, "severity").lower()
        if severity not in _ALLOWED_SEVERITIES:
            allowed = ", ".join(sorted(_ALLOWED_SEVERITIES))
            raise FailureTaxonomyError(
                f"Unsupported signal severity {severity}. Allowed: {allowed}"
            )

        normalized.append(
            FailureSignal(
                signal_id=signal_id,
                source_id=_normalize_required(signal.source_id, "source_id").lower(),
                metric_id=_normalize_required(signal.metric_id, "metric_id").lower(),
                severity=severity,
                description=_normalize_required(signal.description, "description"),
                observed_value=(float(signal.observed_value) if signal.observed_value is not None else None),
                expected_value=(float(signal.expected_value) if signal.expected_value is not None else None),
                metadata=dict(signal.metadata),
            )
        )

    return tuple(sorted(normalized, key=lambda item: item.signal_id))


def _build_signal_text(signal: FailureSignal) -> str:
    metadata_tokens: list[str] = []
    for key, value in sorted(signal.metadata.items()):
        metadata_tokens.append(str(key))
        metadata_tokens.append(str(value))

    observed_tokens: list[str] = []
    if signal.observed_value is not None:
        observed_tokens.append(f"observed_{signal.observed_value}")
    if signal.expected_value is not None:
        observed_tokens.append(f"expected_{signal.expected_value}")

    return " ".join(
        [
            signal.source_id,
            signal.metric_id,
            signal.description.lower(),
            *metadata_tokens,
            *observed_tokens,
        ]
    ).lower()


def _severity_factor(severity: str) -> float:
    if severity == "critical":
        return 1.45
    if severity == "high":
        return 1.25
    if severity == "medium":
        return 1.0
    return 0.8


def _derive_label_severity(
    *,
    default_severity: str,
    signals: list[FailureSignal],
) -> str:
    ranking = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    severity = default_severity
    for signal in signals:
        if ranking[signal.severity] > ranking[severity]:
            severity = signal.severity
    return severity


def _build_rationale(
    root_cause_id: str,
    keyword_hits_by_signal: dict[str, list[str]],
) -> str:
    segments = []
    for signal_id in sorted(keyword_hits_by_signal):
        hits = ", ".join(keyword_hits_by_signal[signal_id][:4])
        segments.append(f"{signal_id} matched [{hits}]")

    details = "; ".join(segments)
    return f"{root_cause_id} selected because {details}" if details else f"{root_cause_id} selected"


def _build_label_id(
    *,
    root_cause_id: str,
    signal_ids: tuple[str, ...],
) -> str:
    canonical = json.dumps(
        {
            "root_cause_id": root_cause_id,
            "signal_ids": list(signal_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"label-{sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _build_report_id(
    *,
    taxonomy_version: str,
    signal_ids: tuple[str, ...],
    label_ids: tuple[str, ...],
) -> str:
    canonical = json.dumps(
        {
            "taxonomy_version": taxonomy_version,
            "signal_ids": list(signal_ids),
            "label_ids": list(label_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"failure-report-{sha256(canonical.encode('utf-8')).hexdigest()[:20]}"


def _normalize_identifier_tuple(
    values: list[str] | tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        item = _normalize_required(value, field_name).lower()
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)

    return tuple(sorted(normalized))


def _violation_severity(level: str) -> str:
    normalized_level = _normalize_required(level, "level").lower()
    if normalized_level == "integrity":
        return "critical"
    if normalized_level in {"overall", "domain"}:
        return "high"
    return "medium"


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise FailureTaxonomyError(f"{field_name} is required")
    return normalized


def _parse_iso_timestamp(value: str) -> datetime:
    normalized = _normalize_required(value, "created_at")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "FailureCategoryDefinition",
    "FailureRootCauseDefinition",
    "FailureTaxonomy",
    "FailureSignal",
    "FailureRootCauseLabel",
    "FailureLabelingReport",
    "FailureTaxonomyError",
    "FailureRootCauseLabeler",
    "build_default_failure_taxonomy",
    "validate_failure_taxonomy",
]
