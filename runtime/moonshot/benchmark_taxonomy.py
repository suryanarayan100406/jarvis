"""Benchmark taxonomy definitions for moonshot capability evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_REQUIRED_DOMAIN_IDS = {"reasoning", "planning", "memory", "tool_use"}


@dataclass(frozen=True)
class BenchmarkDomainDefinition:
    domain_id: str
    title: str
    description: str
    weight: float
    capability_ids: tuple[str, ...]
    target_metrics: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkCapabilityDefinition:
    capability_id: str
    domain_id: str
    title: str
    description: str
    scenario_families: tuple[str, ...]
    core_metrics: tuple[str, ...]
    weight: float


@dataclass(frozen=True)
class BenchmarkDifficultyBand:
    band_id: str
    label: str
    min_score: float
    max_score: float
    multiplier: float


@dataclass(frozen=True)
class BenchmarkTaxonomy:
    taxonomy_version: str
    created_at: str
    domains: tuple[BenchmarkDomainDefinition, ...]
    capabilities: tuple[BenchmarkCapabilityDefinition, ...]
    difficulty_bands: tuple[BenchmarkDifficultyBand, ...]
    metadata: dict[str, Any]

    def get_domain(self, domain_id: str) -> BenchmarkDomainDefinition:
        normalized_domain_id = _normalize_required(domain_id, "domain_id").lower()
        for domain in self.domains:
            if domain.domain_id == normalized_domain_id:
                return domain
        raise KeyError(f"Unknown benchmark domain: {normalized_domain_id}")

    def list_capabilities(self, *, domain_id: str | None = None) -> list[BenchmarkCapabilityDefinition]:
        if domain_id is None:
            return sorted(self.capabilities, key=lambda item: item.capability_id)

        normalized_domain_id = _normalize_required(domain_id, "domain_id").lower()
        return sorted(
            [item for item in self.capabilities if item.domain_id == normalized_domain_id],
            key=lambda item: item.capability_id,
        )

    def to_manifest(self) -> dict[str, Any]:
        return {
            "taxonomy_version": self.taxonomy_version,
            "created_at": self.created_at,
            "domains": [
                {
                    "domain_id": domain.domain_id,
                    "title": domain.title,
                    "description": domain.description,
                    "weight": domain.weight,
                    "capability_ids": list(domain.capability_ids),
                    "target_metrics": list(domain.target_metrics),
                }
                for domain in sorted(self.domains, key=lambda item: item.domain_id)
            ],
            "capabilities": [
                {
                    "capability_id": capability.capability_id,
                    "domain_id": capability.domain_id,
                    "title": capability.title,
                    "description": capability.description,
                    "scenario_families": list(capability.scenario_families),
                    "core_metrics": list(capability.core_metrics),
                    "weight": capability.weight,
                }
                for capability in sorted(self.capabilities, key=lambda item: item.capability_id)
            ],
            "difficulty_bands": [
                {
                    "band_id": band.band_id,
                    "label": band.label,
                    "min_score": band.min_score,
                    "max_score": band.max_score,
                    "multiplier": band.multiplier,
                }
                for band in sorted(self.difficulty_bands, key=lambda item: (item.min_score, item.band_id))
            ],
            "metadata": dict(self.metadata),
        }


class BenchmarkTaxonomyError(ValueError):
    """Raised when benchmark taxonomy configuration is invalid."""


def build_default_benchmark_taxonomy() -> BenchmarkTaxonomy:
    capabilities = (
        BenchmarkCapabilityDefinition(
            capability_id="analogical_reasoning",
            domain_id="reasoning",
            title="Analogical Reasoning",
            description="Transfer patterns from solved examples to novel variants.",
            scenario_families=("pattern_transfer", "counterexample_pressure"),
            core_metrics=("accuracy", "explanation_fidelity"),
            weight=0.34,
        ),
        BenchmarkCapabilityDefinition(
            capability_id="causal_inference",
            domain_id="reasoning",
            title="Causal Inference",
            description="Infer cause-effect relationships under noisy evidence.",
            scenario_families=("ablation_reasoning", "intervention_prediction"),
            core_metrics=("accuracy", "calibration"),
            weight=0.33,
        ),
        BenchmarkCapabilityDefinition(
            capability_id="uncertainty_reasoning",
            domain_id="reasoning",
            title="Uncertainty Reasoning",
            description="Maintain calibrated confidence under ambiguity.",
            scenario_families=("ambiguity_resolution", "confidence_estimation"),
            core_metrics=("calibration", "robustness"),
            weight=0.33,
        ),
        BenchmarkCapabilityDefinition(
            capability_id="decomposition_planning",
            domain_id="planning",
            title="Task Decomposition",
            description="Break complex goals into ordered executable plans.",
            scenario_families=("goal_decomposition", "dependency_ordering"),
            core_metrics=("plan_validity", "step_efficiency"),
            weight=0.34,
        ),
        BenchmarkCapabilityDefinition(
            capability_id="contingency_replanning",
            domain_id="planning",
            title="Contingency Replanning",
            description="Adapt plans after failures without violating constraints.",
            scenario_families=("failure_recovery", "constraint_repair"),
            core_metrics=("recovery_quality", "constraint_compliance"),
            weight=0.33,
        ),
        BenchmarkCapabilityDefinition(
            capability_id="long_horizon_tracking",
            domain_id="planning",
            title="Long-Horizon Tracking",
            description="Preserve objective coherence over long task horizons.",
            scenario_families=("multi_stage_missions", "delayed_dependency_completion"),
            core_metrics=("goal_retention", "completion_quality"),
            weight=0.33,
        ),
        BenchmarkCapabilityDefinition(
            capability_id="episodic_recall",
            domain_id="memory",
            title="Episodic Recall",
            description="Recover prior events with temporal and factual precision.",
            scenario_families=("session_recall", "timeline_reconstruction"),
            core_metrics=("recall_accuracy", "citation_coverage"),
            weight=0.34,
        ),
        BenchmarkCapabilityDefinition(
            capability_id="preference_consistency",
            domain_id="memory",
            title="Preference Consistency",
            description="Retain and apply user preferences across contexts.",
            scenario_families=("style_memory", "policy_preference_resolution"),
            core_metrics=("consistency", "override_safety"),
            weight=0.33,
        ),
        BenchmarkCapabilityDefinition(
            capability_id="retrieval_grounding",
            domain_id="memory",
            title="Retrieval Grounding",
            description="Bind responses to retrievable evidence and citations.",
            scenario_families=("source_grounded_answers", "citation_chain_validation"),
            core_metrics=("groundedness", "citation_precision"),
            weight=0.33,
        ),
        BenchmarkCapabilityDefinition(
            capability_id="tool_selection",
            domain_id="tool_use",
            title="Tool Selection",
            description="Pick the correct tool under interface and policy constraints.",
            scenario_families=("tool_routing", "policy_scoped_selection"),
            core_metrics=("selection_accuracy", "policy_compliance"),
            weight=0.34,
        ),
        BenchmarkCapabilityDefinition(
            capability_id="schema_constrained_invocation",
            domain_id="tool_use",
            title="Schema-Constrained Invocation",
            description="Form valid structured requests with deterministic arguments.",
            scenario_families=("schema_binding", "argument_normalization"),
            core_metrics=("schema_validity", "determinism"),
            weight=0.33,
        ),
        BenchmarkCapabilityDefinition(
            capability_id="multi_tool_coordination",
            domain_id="tool_use",
            title="Multi-Tool Coordination",
            description="Compose reliable multi-step tool workflows with recovery paths.",
            scenario_families=("workflow_orchestration", "cross_tool_state_sync"),
            core_metrics=("workflow_success", "rollback_correctness"),
            weight=0.33,
        ),
    )

    domains = (
        BenchmarkDomainDefinition(
            domain_id="reasoning",
            title="Reasoning",
            description="Inference quality under ambiguity, causality, and uncertainty.",
            weight=0.28,
            capability_ids=(
                "analogical_reasoning",
                "causal_inference",
                "uncertainty_reasoning",
            ),
            target_metrics=("accuracy", "calibration", "robustness"),
        ),
        BenchmarkDomainDefinition(
            domain_id="planning",
            title="Planning",
            description="Plan synthesis and adaptation under constraints and long horizons.",
            weight=0.28,
            capability_ids=(
                "decomposition_planning",
                "contingency_replanning",
                "long_horizon_tracking",
            ),
            target_metrics=("plan_validity", "recovery_quality", "goal_retention"),
        ),
        BenchmarkDomainDefinition(
            domain_id="memory",
            title="Memory",
            description="Context continuity, preference consistency, and citation grounding.",
            weight=0.22,
            capability_ids=(
                "episodic_recall",
                "preference_consistency",
                "retrieval_grounding",
            ),
            target_metrics=("recall_accuracy", "consistency", "groundedness"),
        ),
        BenchmarkDomainDefinition(
            domain_id="tool_use",
            title="Tool Use",
            description="Accurate, policy-safe, and deterministic tool execution behavior.",
            weight=0.22,
            capability_ids=(
                "tool_selection",
                "schema_constrained_invocation",
                "multi_tool_coordination",
            ),
            target_metrics=("selection_accuracy", "schema_validity", "workflow_success"),
        ),
    )

    difficulty_bands = (
        BenchmarkDifficultyBand(
            band_id="baseline",
            label="Baseline",
            min_score=0.00,
            max_score=0.49,
            multiplier=1.00,
        ),
        BenchmarkDifficultyBand(
            band_id="advanced",
            label="Advanced",
            min_score=0.50,
            max_score=0.79,
            multiplier=1.25,
        ),
        BenchmarkDifficultyBand(
            band_id="frontier",
            label="Frontier",
            min_score=0.80,
            max_score=1.00,
            multiplier=1.50,
        ),
    )

    taxonomy = BenchmarkTaxonomy(
        taxonomy_version="1.0.0",
        created_at=_utc_now_iso(),
        domains=domains,
        capabilities=capabilities,
        difficulty_bands=difficulty_bands,
        metadata={
            "program": "moonshot_capability",
            "phase": "P10-T1",
            "notes": "Baseline taxonomy for benchmark harness composition.",
        },
    )
    validate_benchmark_taxonomy(taxonomy)
    return taxonomy


def validate_benchmark_taxonomy(taxonomy: BenchmarkTaxonomy) -> None:
    if not isinstance(taxonomy, BenchmarkTaxonomy):
        raise TypeError("taxonomy must be BenchmarkTaxonomy")

    _parse_iso_timestamp(taxonomy.created_at)
    _normalize_required(taxonomy.taxonomy_version, "taxonomy_version")

    if not taxonomy.domains:
        raise BenchmarkTaxonomyError("taxonomy must include at least one domain")
    if not taxonomy.capabilities:
        raise BenchmarkTaxonomyError("taxonomy must include at least one capability")

    domains_by_id: dict[str, BenchmarkDomainDefinition] = {}
    domain_weight_total = 0.0
    for domain in taxonomy.domains:
        normalized_domain_id = _normalize_required(domain.domain_id, "domain_id").lower()
        if normalized_domain_id in domains_by_id:
            raise BenchmarkTaxonomyError(f"Duplicate domain_id: {normalized_domain_id}")

        if domain.weight <= 0:
            raise BenchmarkTaxonomyError(f"Domain {normalized_domain_id} weight must be positive")
        if not domain.capability_ids:
            raise BenchmarkTaxonomyError(f"Domain {normalized_domain_id} must include capability_ids")
        if not domain.target_metrics:
            raise BenchmarkTaxonomyError(f"Domain {normalized_domain_id} must include target_metrics")

        domains_by_id[normalized_domain_id] = domain
        domain_weight_total += domain.weight

    missing_domains = sorted(_REQUIRED_DOMAIN_IDS - set(domains_by_id))
    if missing_domains:
        raise BenchmarkTaxonomyError(
            "taxonomy is missing required domains: " + ", ".join(missing_domains)
        )

    if abs(domain_weight_total - 1.0) > 1e-6:
        raise BenchmarkTaxonomyError(
            f"Domain weights must sum to 1.0, got {domain_weight_total:.6f}"
        )

    capabilities_by_id: dict[str, BenchmarkCapabilityDefinition] = {}
    domain_capability_weights: dict[str, float] = {domain_id: 0.0 for domain_id in domains_by_id}

    for capability in taxonomy.capabilities:
        normalized_capability_id = _normalize_required(capability.capability_id, "capability_id").lower()
        if normalized_capability_id in capabilities_by_id:
            raise BenchmarkTaxonomyError(f"Duplicate capability_id: {normalized_capability_id}")

        normalized_domain_id = _normalize_required(capability.domain_id, "capability.domain_id").lower()
        if normalized_domain_id not in domains_by_id:
            raise BenchmarkTaxonomyError(
                f"Capability {normalized_capability_id} references unknown domain {normalized_domain_id}"
            )

        if capability.weight <= 0:
            raise BenchmarkTaxonomyError(
                f"Capability {normalized_capability_id} weight must be positive"
            )
        if not capability.scenario_families:
            raise BenchmarkTaxonomyError(
                f"Capability {normalized_capability_id} must include scenario_families"
            )
        if not capability.core_metrics:
            raise BenchmarkTaxonomyError(
                f"Capability {normalized_capability_id} must include core_metrics"
            )

        capabilities_by_id[normalized_capability_id] = capability
        domain_capability_weights[normalized_domain_id] += capability.weight

    unreferenced_capabilities = set(capabilities_by_id)
    for domain in taxonomy.domains:
        normalized_domain_id = domain.domain_id.lower()
        seen_ids: set[str] = set()

        for raw_capability_id in domain.capability_ids:
            normalized_capability_id = _normalize_required(raw_capability_id, "domain.capability_id").lower()
            if normalized_capability_id in seen_ids:
                raise BenchmarkTaxonomyError(
                    f"Domain {normalized_domain_id} contains duplicate capability {normalized_capability_id}"
                )
            capability = capabilities_by_id.get(normalized_capability_id)
            if capability is None:
                raise BenchmarkTaxonomyError(
                    f"Domain {normalized_domain_id} references unknown capability {normalized_capability_id}"
                )
            if capability.domain_id != normalized_domain_id:
                raise BenchmarkTaxonomyError(
                    f"Capability {normalized_capability_id} domain mismatch: expected {normalized_domain_id}, got {capability.domain_id}"
                )

            seen_ids.add(normalized_capability_id)
            unreferenced_capabilities.discard(normalized_capability_id)

    if unreferenced_capabilities:
        raise BenchmarkTaxonomyError(
            "Capabilities not mapped to domains: " + ", ".join(sorted(unreferenced_capabilities))
        )

    for domain_id, total_weight in domain_capability_weights.items():
        if abs(total_weight - 1.0) > 1e-6:
            raise BenchmarkTaxonomyError(
                f"Capability weights for domain {domain_id} must sum to 1.0, got {total_weight:.6f}"
            )

    if not taxonomy.difficulty_bands:
        raise BenchmarkTaxonomyError("taxonomy must include difficulty_bands")

    sorted_bands = sorted(taxonomy.difficulty_bands, key=lambda item: (item.min_score, item.band_id))
    band_ids: set[str] = set()
    previous_max: float | None = None

    for band in sorted_bands:
        normalized_band_id = _normalize_required(band.band_id, "band_id").lower()
        if normalized_band_id in band_ids:
            raise BenchmarkTaxonomyError(f"Duplicate band_id: {normalized_band_id}")
        band_ids.add(normalized_band_id)

        if band.multiplier <= 0:
            raise BenchmarkTaxonomyError(
                f"Difficulty band {normalized_band_id} multiplier must be positive"
            )
        if band.min_score < 0.0 or band.max_score > 1.0:
            raise BenchmarkTaxonomyError(
                f"Difficulty band {normalized_band_id} score range must stay within [0.0, 1.0]"
            )
        if band.min_score > band.max_score:
            raise BenchmarkTaxonomyError(
                f"Difficulty band {normalized_band_id} has invalid score range"
            )
        if previous_max is not None and band.min_score < previous_max:
            raise BenchmarkTaxonomyError(
                f"Difficulty bands overlap at {normalized_band_id}"
            )

        previous_max = band.max_score

    if abs(sorted_bands[0].min_score - 0.0) > 1e-6 or abs(sorted_bands[-1].max_score - 1.0) > 1e-6:
        raise BenchmarkTaxonomyError("Difficulty bands must cover score range from 0.0 to 1.0")


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise BenchmarkTaxonomyError(f"{field_name} is required")
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
