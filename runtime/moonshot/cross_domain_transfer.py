"""Cross-domain transfer task definitions for moonshot evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .benchmark_harness import BenchmarkScenarioDefinition
from .benchmark_taxonomy import BenchmarkTaxonomy, build_default_benchmark_taxonomy, validate_benchmark_taxonomy

_ALLOWED_DIFFICULTY_BANDS = {"baseline", "advanced", "frontier"}
_REQUIRED_DOMAIN_IDS = {"reasoning", "planning", "memory", "tool_use"}
_MIN_DOMAIN_PAIR_COVERAGE = 4


@dataclass(frozen=True)
class CrossDomainTransferCheckpoint:
    checkpoint_id: str
    phase_index: int
    source_domain_id: str
    target_domain_id: str
    source_capability_id: str
    target_capability_id: str
    transfer_objective: str
    required_tools: tuple[str, ...]
    success_metrics: tuple[str, ...]
    max_turns: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CrossDomainTransferScenario:
    scenario_id: str
    title: str
    description: str
    source_domain_id: str
    target_domain_id: str
    source_anchor_capability_id: str
    primary_target_capability_id: str
    transfer_turns: int
    checkpoints: tuple[CrossDomainTransferCheckpoint, ...]
    completion_metrics: tuple[str, ...]
    metadata: dict[str, Any]

    def get_checkpoint(self, checkpoint_id: str) -> CrossDomainTransferCheckpoint:
        normalized_checkpoint_id = _normalize_required(checkpoint_id, "checkpoint_id").lower()
        for checkpoint in self.checkpoints:
            if checkpoint.checkpoint_id == normalized_checkpoint_id:
                return checkpoint
        raise KeyError(f"Unknown transfer checkpoint: {normalized_checkpoint_id}")


@dataclass(frozen=True)
class CrossDomainTransferEvaluationSuite:
    suite_version: str
    created_at: str
    scenarios: tuple[CrossDomainTransferScenario, ...]
    metadata: dict[str, Any]

    def get_scenario(self, scenario_id: str) -> CrossDomainTransferScenario:
        normalized_scenario_id = _normalize_required(scenario_id, "scenario_id").lower()
        for scenario in self.scenarios:
            if scenario.scenario_id == normalized_scenario_id:
                return scenario
        raise KeyError(f"Unknown cross-domain transfer scenario: {normalized_scenario_id}")

    def list_scenarios(
        self,
        *,
        source_domain_id: str | None = None,
        target_domain_id: str | None = None,
    ) -> list[CrossDomainTransferScenario]:
        filtered = list(self.scenarios)

        if source_domain_id is not None:
            normalized_source_domain_id = _normalize_required(source_domain_id, "source_domain_id").lower()
            filtered = [item for item in filtered if item.source_domain_id == normalized_source_domain_id]

        if target_domain_id is not None:
            normalized_target_domain_id = _normalize_required(target_domain_id, "target_domain_id").lower()
            filtered = [item for item in filtered if item.target_domain_id == normalized_target_domain_id]

        return sorted(filtered, key=lambda item: item.scenario_id)

    def to_benchmark_scenarios(
        self,
        *,
        default_difficulty_band: str = "advanced",
    ) -> list[BenchmarkScenarioDefinition]:
        normalized_difficulty_band = _normalize_required(
            default_difficulty_band,
            "default_difficulty_band",
        ).lower()
        if normalized_difficulty_band not in _ALLOWED_DIFFICULTY_BANDS:
            allowed = ", ".join(sorted(_ALLOWED_DIFFICULTY_BANDS))
            raise CrossDomainTransferError(
                f"Unsupported difficulty band {normalized_difficulty_band}. Allowed: {allowed}"
            )

        benchmark_scenarios: list[BenchmarkScenarioDefinition] = []
        for scenario in sorted(self.scenarios, key=lambda item: item.scenario_id):
            benchmark_scenarios.append(
                BenchmarkScenarioDefinition(
                    scenario_id=f"xd-{scenario.scenario_id}",
                    capability_id=scenario.primary_target_capability_id,
                    difficulty_band_id=normalized_difficulty_band,
                    prompt=scenario.description,
                    weight=_derive_scenario_weight(scenario),
                    metadata={
                        "source_domain_id": scenario.source_domain_id,
                        "target_domain_id": scenario.target_domain_id,
                        "source_anchor_capability_id": scenario.source_anchor_capability_id,
                        "target_capability_id": scenario.primary_target_capability_id,
                        "checkpoint_count": len(scenario.checkpoints),
                        "transfer_turns": scenario.transfer_turns,
                        "completion_metrics": list(scenario.completion_metrics),
                        **dict(scenario.metadata),
                    },
                )
            )

        return benchmark_scenarios


class CrossDomainTransferError(ValueError):
    """Raised when cross-domain transfer scenarios violate required constraints."""


def build_default_cross_domain_transfer_suite(
    taxonomy: BenchmarkTaxonomy | None = None,
) -> CrossDomainTransferEvaluationSuite:
    if taxonomy is None:
        taxonomy = build_default_benchmark_taxonomy()
    validate_benchmark_taxonomy(taxonomy)

    scenarios = (
        CrossDomainTransferScenario(
            scenario_id="causal_to_replan_incident_bridge",
            title="Causal-to-Replan Incident Bridge",
            description=(
                "Transfer causal diagnostics into actionable replanning under evolving outage pressure "
                "while preserving operational constraints and rollback safety."
            ),
            source_domain_id="reasoning",
            target_domain_id="planning",
            source_anchor_capability_id="causal_inference",
            primary_target_capability_id="contingency_replanning",
            transfer_turns=12,
            checkpoints=(
                CrossDomainTransferCheckpoint(
                    checkpoint_id="evidence_modeling",
                    phase_index=1,
                    source_domain_id="reasoning",
                    target_domain_id="planning",
                    source_capability_id="causal_inference",
                    target_capability_id="contingency_replanning",
                    transfer_objective="Infer fault drivers and encode them as replanning constraints.",
                    required_tools=("event_bus", "risk_overlay"),
                    success_metrics=("accuracy", "constraint_compliance"),
                    max_turns=4,
                    metadata={},
                ),
                CrossDomainTransferCheckpoint(
                    checkpoint_id="branch_strategy_selection",
                    phase_index=2,
                    source_domain_id="reasoning",
                    target_domain_id="planning",
                    source_capability_id="uncertainty_reasoning",
                    target_capability_id="decomposition_planning",
                    transfer_objective="Translate uncertainty bounds into branch-ready response plans.",
                    required_tools=("runbook_engine", "status_cli"),
                    success_metrics=("calibration", "plan_validity"),
                    max_turns=4,
                    metadata={},
                ),
                CrossDomainTransferCheckpoint(
                    checkpoint_id="replan_validation",
                    phase_index=3,
                    source_domain_id="reasoning",
                    target_domain_id="planning",
                    source_capability_id="analogical_reasoning",
                    target_capability_id="contingency_replanning",
                    transfer_objective="Apply prior remediation analogies to validate selected replan path.",
                    required_tools=("run_replay", "result_reporter"),
                    success_metrics=("recovery_quality", "goal_retention"),
                    max_turns=3,
                    metadata={},
                ),
            ),
            completion_metrics=("plan_validity", "recovery_quality", "constraint_compliance"),
            metadata={"track": "incident_ops"},
        ),
        CrossDomainTransferScenario(
            scenario_id="decomposition_to_orchestration_bridge",
            title="Decomposition-to-Orchestration Bridge",
            description=(
                "Carry decomposition plans into reliable multi-tool orchestration with schema-safe invocation "
                "and deterministic rollback checkpoints."
            ),
            source_domain_id="planning",
            target_domain_id="tool_use",
            source_anchor_capability_id="decomposition_planning",
            primary_target_capability_id="multi_tool_coordination",
            transfer_turns=11,
            checkpoints=(
                CrossDomainTransferCheckpoint(
                    checkpoint_id="dependency_projection",
                    phase_index=1,
                    source_domain_id="planning",
                    target_domain_id="tool_use",
                    source_capability_id="decomposition_planning",
                    target_capability_id="tool_selection",
                    transfer_objective="Project plan dependencies into tool-routing prerequisites.",
                    required_tools=("tool_registry", "policy_overlay"),
                    success_metrics=("selection_accuracy", "plan_validity"),
                    max_turns=3,
                    metadata={},
                ),
                CrossDomainTransferCheckpoint(
                    checkpoint_id="schema_binding",
                    phase_index=2,
                    source_domain_id="planning",
                    target_domain_id="tool_use",
                    source_capability_id="contingency_replanning",
                    target_capability_id="schema_constrained_invocation",
                    transfer_objective="Bind fallback branches into valid schema-constrained action envelopes.",
                    required_tools=("command_templates", "connector_health"),
                    success_metrics=("schema_validity", "determinism"),
                    max_turns=4,
                    metadata={},
                ),
                CrossDomainTransferCheckpoint(
                    checkpoint_id="orchestration_run",
                    phase_index=3,
                    source_domain_id="planning",
                    target_domain_id="tool_use",
                    source_capability_id="long_horizon_tracking",
                    target_capability_id="multi_tool_coordination",
                    transfer_objective="Execute an ordered workflow while preserving rollback and checkpoint semantics.",
                    required_tools=("parallel_orchestrator", "rollback_actions"),
                    success_metrics=("workflow_success", "rollback_correctness"),
                    max_turns=3,
                    metadata={},
                ),
            ),
            completion_metrics=("workflow_success", "schema_validity", "determinism"),
            metadata={"track": "orchestration"},
        ),
        CrossDomainTransferScenario(
            scenario_id="retrieval_to_causal_hypothesis_bridge",
            title="Retrieval-to-Causal Hypothesis Bridge",
            description=(
                "Transfer grounded memory evidence into robust causal hypotheses and calibrated reasoning under "
                "conflicting historical signals."
            ),
            source_domain_id="memory",
            target_domain_id="reasoning",
            source_anchor_capability_id="retrieval_grounding",
            primary_target_capability_id="causal_inference",
            transfer_turns=10,
            checkpoints=(
                CrossDomainTransferCheckpoint(
                    checkpoint_id="evidence_alignment",
                    phase_index=1,
                    source_domain_id="memory",
                    target_domain_id="reasoning",
                    source_capability_id="retrieval_grounding",
                    target_capability_id="causal_inference",
                    transfer_objective="Consolidate evidence snippets into structured causal claims.",
                    required_tools=("memory_retrieval", "evidence_ranker"),
                    success_metrics=("groundedness", "accuracy"),
                    max_turns=3,
                    metadata={},
                ),
                CrossDomainTransferCheckpoint(
                    checkpoint_id="temporal_reconstruction",
                    phase_index=2,
                    source_domain_id="memory",
                    target_domain_id="reasoning",
                    source_capability_id="episodic_recall",
                    target_capability_id="analogical_reasoning",
                    transfer_objective="Reconstruct event chronology and compare against known failure archetypes.",
                    required_tools=("run_replay", "memory_index"),
                    success_metrics=("recall_accuracy", "explanation_fidelity"),
                    max_turns=3,
                    metadata={},
                ),
                CrossDomainTransferCheckpoint(
                    checkpoint_id="confidence_calibration",
                    phase_index=3,
                    source_domain_id="memory",
                    target_domain_id="reasoning",
                    source_capability_id="preference_consistency",
                    target_capability_id="uncertainty_reasoning",
                    transfer_objective="Adjust confidence levels using persistent preference and evidence quality context.",
                    required_tools=("status_summary", "reporter"),
                    success_metrics=("calibration", "robustness"),
                    max_turns=3,
                    metadata={},
                ),
            ),
            completion_metrics=("accuracy", "calibration", "groundedness"),
            metadata={"track": "analysis"},
        ),
        CrossDomainTransferScenario(
            scenario_id="tool_trace_to_memory_consistency_bridge",
            title="Tool Trace-to-Memory Consistency Bridge",
            description=(
                "Transfer tool-execution traces into durable memory updates that preserve citation coverage and "
                "cross-session consistency guarantees."
            ),
            source_domain_id="tool_use",
            target_domain_id="memory",
            source_anchor_capability_id="schema_constrained_invocation",
            primary_target_capability_id="retrieval_grounding",
            transfer_turns=10,
            checkpoints=(
                CrossDomainTransferCheckpoint(
                    checkpoint_id="trace_capture",
                    phase_index=1,
                    source_domain_id="tool_use",
                    target_domain_id="memory",
                    source_capability_id="schema_constrained_invocation",
                    target_capability_id="retrieval_grounding",
                    transfer_objective="Capture normalized tool traces and map them to citation-ready memory artifacts.",
                    required_tools=("result_reporter", "memory_index"),
                    success_metrics=("schema_validity", "citation_coverage"),
                    max_turns=3,
                    metadata={},
                ),
                CrossDomainTransferCheckpoint(
                    checkpoint_id="conflict_reconciliation",
                    phase_index=2,
                    source_domain_id="tool_use",
                    target_domain_id="memory",
                    source_capability_id="multi_tool_coordination",
                    target_capability_id="preference_consistency",
                    transfer_objective="Resolve cross-tool conflicts while preserving user preference invariants.",
                    required_tools=("preference_store", "policy_overlay"),
                    success_metrics=("consistency", "override_safety"),
                    max_turns=3,
                    metadata={},
                ),
                CrossDomainTransferCheckpoint(
                    checkpoint_id="session_handoff",
                    phase_index=3,
                    source_domain_id="tool_use",
                    target_domain_id="memory",
                    source_capability_id="tool_selection",
                    target_capability_id="episodic_recall",
                    transfer_objective="Store replayable execution context for resilient next-session recall.",
                    required_tools=("run_replay", "memory_retrieval"),
                    success_metrics=("selection_accuracy", "recall_accuracy"),
                    max_turns=3,
                    metadata={},
                ),
            ),
            completion_metrics=("citation_coverage", "consistency", "recall_accuracy"),
            metadata={"track": "memory_ops"},
        ),
    )

    suite = CrossDomainTransferEvaluationSuite(
        suite_version="1.0.0",
        created_at=_utc_now_iso(),
        scenarios=tuple(sorted(scenarios, key=lambda item: item.scenario_id)),
        metadata={
            "program": "moonshot_capability",
            "phase": "P10-T4",
            "notes": "Baseline cross-domain transfer evaluation suite for benchmark expansion.",
        },
    )
    validate_cross_domain_transfer_suite(suite, taxonomy=taxonomy)
    return suite


def validate_cross_domain_transfer_suite(
    suite: CrossDomainTransferEvaluationSuite,
    *,
    taxonomy: BenchmarkTaxonomy | None = None,
) -> None:
    if not isinstance(suite, CrossDomainTransferEvaluationSuite):
        raise TypeError("suite must be CrossDomainTransferEvaluationSuite")

    if taxonomy is None:
        taxonomy = build_default_benchmark_taxonomy()
    validate_benchmark_taxonomy(taxonomy)

    _normalize_required(suite.suite_version, "suite_version")
    _parse_iso_timestamp(suite.created_at)

    if not suite.scenarios:
        raise CrossDomainTransferError("suite must include at least one scenario")

    domains_by_id = {domain.domain_id: domain for domain in taxonomy.domains}
    capability_by_id = {capability.capability_id: capability for capability in taxonomy.capabilities}

    missing_domains = sorted(_REQUIRED_DOMAIN_IDS - set(domains_by_id))
    if missing_domains:
        raise CrossDomainTransferError(
            "taxonomy is missing required domains for cross-domain transfer: " + ", ".join(missing_domains)
        )

    scenario_ids: set[str] = set()
    domain_pairs: set[tuple[str, str]] = set()

    for scenario in suite.scenarios:
        scenario_id = _normalize_required(scenario.scenario_id, "scenario_id").lower()
        if scenario_id in scenario_ids:
            raise CrossDomainTransferError(f"Duplicate scenario_id: {scenario_id}")
        scenario_ids.add(scenario_id)

        source_domain_id = _normalize_required(scenario.source_domain_id, "source_domain_id").lower()
        target_domain_id = _normalize_required(scenario.target_domain_id, "target_domain_id").lower()

        if source_domain_id not in domains_by_id:
            raise CrossDomainTransferError(
                f"Scenario {scenario.scenario_id} references unknown source domain {source_domain_id}"
            )
        if target_domain_id not in domains_by_id:
            raise CrossDomainTransferError(
                f"Scenario {scenario.scenario_id} references unknown target domain {target_domain_id}"
            )
        if source_domain_id == target_domain_id:
            raise CrossDomainTransferError(
                f"Scenario {scenario.scenario_id} source and target domains must differ"
            )

        domain_pairs.add((source_domain_id, target_domain_id))

        source_anchor = _normalize_required(
            scenario.source_anchor_capability_id,
            "source_anchor_capability_id",
        ).lower()
        source_anchor_definition = capability_by_id.get(source_anchor)
        if source_anchor_definition is None:
            raise CrossDomainTransferError(
                f"Scenario {scenario.scenario_id} references unknown source anchor capability {source_anchor}"
            )
        if source_anchor_definition.domain_id != source_domain_id:
            raise CrossDomainTransferError(
                f"Scenario {scenario.scenario_id} source anchor capability {source_anchor} is not in domain {source_domain_id}"
            )

        target_capability = _normalize_required(
            scenario.primary_target_capability_id,
            "primary_target_capability_id",
        ).lower()
        target_capability_definition = capability_by_id.get(target_capability)
        if target_capability_definition is None:
            raise CrossDomainTransferError(
                f"Scenario {scenario.scenario_id} references unknown target capability {target_capability}"
            )
        if target_capability_definition.domain_id != target_domain_id:
            raise CrossDomainTransferError(
                f"Scenario {scenario.scenario_id} target capability {target_capability} is not in domain {target_domain_id}"
            )

        if scenario.transfer_turns < 2:
            raise CrossDomainTransferError(
                f"Scenario {scenario.scenario_id} must allow at least 2 transfer turns"
            )

        if len(scenario.checkpoints) < 2:
            raise CrossDomainTransferError(
                f"Scenario {scenario.scenario_id} must include at least 2 checkpoints"
            )

        if not scenario.completion_metrics:
            raise CrossDomainTransferError(
                f"Scenario {scenario.scenario_id} must include completion_metrics"
            )

        expected_phase_index = 1
        checkpoint_ids: set[str] = set()
        checkpoint_turn_budget = 0

        for checkpoint in scenario.checkpoints:
            checkpoint_id = _normalize_required(checkpoint.checkpoint_id, "checkpoint_id").lower()
            if checkpoint_id in checkpoint_ids:
                raise CrossDomainTransferError(
                    f"Scenario {scenario.scenario_id} has duplicate checkpoint {checkpoint_id}"
                )
            checkpoint_ids.add(checkpoint_id)

            if checkpoint.phase_index != expected_phase_index:
                raise CrossDomainTransferError(
                    f"Scenario {scenario.scenario_id} checkpoint {checkpoint_id} has non-sequential phase_index"
                )
            expected_phase_index += 1

            checkpoint_source_domain = _normalize_required(
                checkpoint.source_domain_id,
                "checkpoint.source_domain_id",
            ).lower()
            checkpoint_target_domain = _normalize_required(
                checkpoint.target_domain_id,
                "checkpoint.target_domain_id",
            ).lower()

            if checkpoint_source_domain != source_domain_id or checkpoint_target_domain != target_domain_id:
                raise CrossDomainTransferError(
                    f"Scenario {scenario.scenario_id} checkpoint {checkpoint_id} domain pair does not match scenario domain pair"
                )
            if checkpoint_source_domain == checkpoint_target_domain:
                raise CrossDomainTransferError(
                    f"Checkpoint {checkpoint_id} source and target domains must differ"
                )

            source_capability_id = _normalize_required(
                checkpoint.source_capability_id,
                "checkpoint.source_capability_id",
            ).lower()
            source_capability = capability_by_id.get(source_capability_id)
            if source_capability is None:
                raise CrossDomainTransferError(
                    f"Checkpoint {checkpoint_id} references unknown source capability {source_capability_id}"
                )
            if source_capability.domain_id != checkpoint_source_domain:
                raise CrossDomainTransferError(
                    f"Checkpoint {checkpoint_id} source capability {source_capability_id} is not in domain {checkpoint_source_domain}"
                )

            target_capability_id = _normalize_required(
                checkpoint.target_capability_id,
                "checkpoint.target_capability_id",
            ).lower()
            target_capability = capability_by_id.get(target_capability_id)
            if target_capability is None:
                raise CrossDomainTransferError(
                    f"Checkpoint {checkpoint_id} references unknown target capability {target_capability_id}"
                )
            if target_capability.domain_id != checkpoint_target_domain:
                raise CrossDomainTransferError(
                    f"Checkpoint {checkpoint_id} target capability {target_capability_id} is not in domain {checkpoint_target_domain}"
                )

            _normalize_required(checkpoint.transfer_objective, "checkpoint.transfer_objective")

            if not checkpoint.required_tools:
                raise CrossDomainTransferError(
                    f"Checkpoint {checkpoint_id} must include required_tools"
                )
            if not checkpoint.success_metrics:
                raise CrossDomainTransferError(
                    f"Checkpoint {checkpoint_id} must include success_metrics"
                )
            if checkpoint.max_turns < 1:
                raise CrossDomainTransferError(
                    f"Checkpoint {checkpoint_id} must allow at least 1 turn"
                )

            checkpoint_turn_budget += checkpoint.max_turns

        if scenario.transfer_turns < checkpoint_turn_budget:
            raise CrossDomainTransferError(
                f"Scenario {scenario.scenario_id} transfer_turns is below checkpoint turn budget"
            )

    if len(domain_pairs) < _MIN_DOMAIN_PAIR_COVERAGE:
        raise CrossDomainTransferError(
            f"suite must include at least {_MIN_DOMAIN_PAIR_COVERAGE} unique source-target domain pairs"
        )


def _derive_scenario_weight(scenario: CrossDomainTransferScenario) -> float:
    base = 1.0
    base += 0.08 * len(scenario.checkpoints)
    base += 0.02 * scenario.transfer_turns
    if scenario.source_domain_id != scenario.target_domain_id:
        base += 0.12
    return round(min(base, 2.5), 6)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise CrossDomainTransferError(f"{field_name} is required")
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
