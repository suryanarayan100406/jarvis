"""Long-horizon mission scenario definitions for moonshot evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .benchmark_harness import BenchmarkScenarioDefinition
from .benchmark_taxonomy import BenchmarkTaxonomy, build_default_benchmark_taxonomy, validate_benchmark_taxonomy

_ALLOWED_DIFFICULTY_BANDS = {"baseline", "advanced", "frontier"}


@dataclass(frozen=True)
class LongHorizonCheckpoint:
    checkpoint_id: str
    phase_index: int
    objective: str
    expected_capability_ids: tuple[str, ...]
    required_tools: tuple[str, ...]
    success_metrics: tuple[str, ...]
    max_turns: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class LongHorizonPerturbation:
    perturbation_id: str
    trigger_checkpoint_id: str
    perturbation_type: str
    severity: float
    description: str
    expected_adaptations: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class LongHorizonMissionScenario:
    scenario_id: str
    title: str
    description: str
    primary_capability_id: str
    horizon_turns: int
    checkpoints: tuple[LongHorizonCheckpoint, ...]
    perturbations: tuple[LongHorizonPerturbation, ...]
    completion_metrics: tuple[str, ...]
    metadata: dict[str, Any]

    def get_checkpoint(self, checkpoint_id: str) -> LongHorizonCheckpoint:
        normalized_checkpoint_id = _normalize_required(checkpoint_id, "checkpoint_id").lower()
        for checkpoint in self.checkpoints:
            if checkpoint.checkpoint_id == normalized_checkpoint_id:
                return checkpoint
        raise KeyError(f"Unknown checkpoint: {normalized_checkpoint_id}")


@dataclass(frozen=True)
class LongHorizonScenarioSuite:
    suite_version: str
    created_at: str
    scenarios: tuple[LongHorizonMissionScenario, ...]
    metadata: dict[str, Any]

    def get_scenario(self, scenario_id: str) -> LongHorizonMissionScenario:
        normalized_scenario_id = _normalize_required(scenario_id, "scenario_id").lower()
        for scenario in self.scenarios:
            if scenario.scenario_id == normalized_scenario_id:
                return scenario
        raise KeyError(f"Unknown long-horizon scenario: {normalized_scenario_id}")

    def list_scenarios(self, *, primary_capability_id: str | None = None) -> list[LongHorizonMissionScenario]:
        if primary_capability_id is None:
            return sorted(self.scenarios, key=lambda item: item.scenario_id)

        normalized_capability_id = _normalize_required(primary_capability_id, "primary_capability_id").lower()
        return sorted(
            [item for item in self.scenarios if item.primary_capability_id == normalized_capability_id],
            key=lambda item: item.scenario_id,
        )

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
            raise LongHorizonScenarioError(
                f"Unsupported difficulty band {normalized_difficulty_band}. Allowed: {allowed}"
            )

        benchmark_scenarios: list[BenchmarkScenarioDefinition] = []
        for scenario in sorted(self.scenarios, key=lambda item: item.scenario_id):
            weight = _derive_scenario_weight(scenario)
            benchmark_scenarios.append(
                BenchmarkScenarioDefinition(
                    scenario_id=f"lh-{scenario.scenario_id}",
                    capability_id=scenario.primary_capability_id,
                    difficulty_band_id=normalized_difficulty_band,
                    prompt=scenario.description,
                    weight=weight,
                    metadata={
                        "horizon_turns": scenario.horizon_turns,
                        "checkpoint_count": len(scenario.checkpoints),
                        "perturbation_count": len(scenario.perturbations),
                        "completion_metrics": list(scenario.completion_metrics),
                        **dict(scenario.metadata),
                    },
                )
            )

        return benchmark_scenarios


class LongHorizonScenarioError(ValueError):
    """Raised when long-horizon mission scenarios violate required constraints."""


def build_default_long_horizon_scenario_suite(
    taxonomy: BenchmarkTaxonomy | None = None,
) -> LongHorizonScenarioSuite:
    if taxonomy is None:
        taxonomy = build_default_benchmark_taxonomy()
    validate_benchmark_taxonomy(taxonomy)

    scenarios = (
        LongHorizonMissionScenario(
            scenario_id="cross_region_outage_recovery",
            title="Cross-Region Outage Recovery",
            description=(
                "Stabilize a multi-region service outage while preserving policy constraints, "
                "restoring critical paths, and producing a recovery summary."
            ),
            primary_capability_id="long_horizon_tracking",
            horizon_turns=18,
            checkpoints=(
                LongHorizonCheckpoint(
                    checkpoint_id="detect_outage",
                    phase_index=1,
                    objective="Correlate cross-region failure signals and identify likely blast radius.",
                    expected_capability_ids=("causal_inference", "tool_selection"),
                    required_tools=("event_bus", "status_cli"),
                    success_metrics=("accuracy", "groundedness"),
                    max_turns=4,
                    metadata={},
                ),
                LongHorizonCheckpoint(
                    checkpoint_id="stabilize_services",
                    phase_index=2,
                    objective="Execute staged mitigation plan with contingency branches.",
                    expected_capability_ids=("contingency_replanning", "multi_tool_coordination"),
                    required_tools=("runbook_engine", "parallel_orchestrator"),
                    success_metrics=("workflow_success", "constraint_compliance"),
                    max_turns=6,
                    metadata={},
                ),
                LongHorizonCheckpoint(
                    checkpoint_id="verify_recovery",
                    phase_index=3,
                    objective="Validate service health and policy compliance across regions.",
                    expected_capability_ids=("retrieval_grounding", "schema_constrained_invocation"),
                    required_tools=("connector_health", "result_reporter"),
                    success_metrics=("policy_compliance", "citation_precision"),
                    max_turns=4,
                    metadata={},
                ),
                LongHorizonCheckpoint(
                    checkpoint_id="postmortem_alignment",
                    phase_index=4,
                    objective="Produce timeline and unresolved-risk register for handoff.",
                    expected_capability_ids=("episodic_recall", "preference_consistency"),
                    required_tools=("memory_retrieval", "reporter"),
                    success_metrics=("recall_accuracy", "completion_quality"),
                    max_turns=3,
                    metadata={},
                ),
            ),
            perturbations=(
                LongHorizonPerturbation(
                    perturbation_id="telemetry_drift",
                    trigger_checkpoint_id="stabilize_services",
                    perturbation_type="data_quality",
                    severity=0.6,
                    description="Partial telemetry drift obscures real-time service status in one region.",
                    expected_adaptations=("fallback_signal_fusion", "confidence_reclassification"),
                    metadata={},
                ),
                LongHorizonPerturbation(
                    perturbation_id="dependency_regression",
                    trigger_checkpoint_id="verify_recovery",
                    perturbation_type="system_fault",
                    severity=0.7,
                    description="A downstream dependency regresses after initial recovery appears successful.",
                    expected_adaptations=("targeted_replan", "rollback_guardrail_check"),
                    metadata={},
                ),
            ),
            completion_metrics=("goal_retention", "workflow_success", "policy_compliance"),
            metadata={"track": "operations"},
        ),
        LongHorizonMissionScenario(
            scenario_id="supply_chain_disruption_response",
            title="Supply Chain Disruption Response",
            description=(
                "Coordinate mitigation for critical part shortages while balancing cost, risk, "
                "and service continuity over multiple planning windows."
            ),
            primary_capability_id="contingency_replanning",
            horizon_turns=15,
            checkpoints=(
                LongHorizonCheckpoint(
                    checkpoint_id="risk_surface_mapping",
                    phase_index=1,
                    objective="Identify exposed services and critical dependency paths.",
                    expected_capability_ids=("decomposition_planning", "causal_inference"),
                    required_tools=("inventory_index", "risk_overlay"),
                    success_metrics=("plan_validity", "accuracy"),
                    max_turns=4,
                    metadata={},
                ),
                LongHorizonCheckpoint(
                    checkpoint_id="mitigation_path_selection",
                    phase_index=2,
                    objective="Select fallback sourcing and execution path under policy constraints.",
                    expected_capability_ids=("tool_selection", "schema_constrained_invocation"),
                    required_tools=("policy_overlay", "command_templates"),
                    success_metrics=("selection_accuracy", "constraint_compliance"),
                    max_turns=5,
                    metadata={},
                ),
                LongHorizonCheckpoint(
                    checkpoint_id="continuity_validation",
                    phase_index=3,
                    objective="Validate continuity assumptions and update unresolved dependencies.",
                    expected_capability_ids=("retrieval_grounding", "episodic_recall"),
                    required_tools=("memory_retrieval", "run_replay"),
                    success_metrics=("groundedness", "goal_retention"),
                    max_turns=4,
                    metadata={},
                ),
            ),
            perturbations=(
                LongHorizonPerturbation(
                    perturbation_id="vendor_withdrawal",
                    trigger_checkpoint_id="mitigation_path_selection",
                    perturbation_type="external_change",
                    severity=0.8,
                    description="Primary fallback vendor withdraws after provisional approval.",
                    expected_adaptations=("alternative_path_reprioritization", "impact_reforecast"),
                    metadata={},
                ),
            ),
            completion_metrics=("recovery_quality", "workflow_success", "robustness"),
            metadata={"track": "logistics"},
        ),
        LongHorizonMissionScenario(
            scenario_id="policy_constrained_release_rollout",
            title="Policy-Constrained Release Rollout",
            description=(
                "Drive phased release rollout with strict policy checks and adaptive coordination "
                "across deployment and validation systems."
            ),
            primary_capability_id="multi_tool_coordination",
            horizon_turns=14,
            checkpoints=(
                LongHorizonCheckpoint(
                    checkpoint_id="release_gate_plan",
                    phase_index=1,
                    objective="Build release sequence with policy checkpoints and rollback anchors.",
                    expected_capability_ids=("decomposition_planning", "schema_constrained_invocation"),
                    required_tools=("policy_overlay", "rollback_actions"),
                    success_metrics=("plan_validity", "determinism"),
                    max_turns=4,
                    metadata={},
                ),
                LongHorizonCheckpoint(
                    checkpoint_id="progressive_execution",
                    phase_index=2,
                    objective="Execute staged rollout while monitoring drift and failure risk.",
                    expected_capability_ids=("multi_tool_coordination", "long_horizon_tracking"),
                    required_tools=("parallel_orchestrator", "connector_health"),
                    success_metrics=("workflow_success", "goal_retention"),
                    max_turns=5,
                    metadata={},
                ),
                LongHorizonCheckpoint(
                    checkpoint_id="final_validation",
                    phase_index=3,
                    objective="Validate release outcomes and generate compliance evidence pack.",
                    expected_capability_ids=("retrieval_grounding", "preference_consistency"),
                    required_tools=("result_reporter", "memory_index"),
                    success_metrics=("citation_precision", "policy_compliance"),
                    max_turns=3,
                    metadata={},
                ),
            ),
            perturbations=(
                LongHorizonPerturbation(
                    perturbation_id="approval_latency_spike",
                    trigger_checkpoint_id="progressive_execution",
                    perturbation_type="governance_delay",
                    severity=0.5,
                    description="Approval response latency increases and threatens rollout window.",
                    expected_adaptations=("schedule_rebaseline", "risk_reprioritization"),
                    metadata={},
                ),
            ),
            completion_metrics=("workflow_success", "policy_compliance", "completion_quality"),
            metadata={"track": "release_engineering"},
        ),
        LongHorizonMissionScenario(
            scenario_id="cross_session_incident_followthrough",
            title="Cross-Session Incident Follow-Through",
            description=(
                "Resume and complete unresolved incident response tasks across session boundaries "
                "with continuity and evidence fidelity."
            ),
            primary_capability_id="episodic_recall",
            horizon_turns=12,
            checkpoints=(
                LongHorizonCheckpoint(
                    checkpoint_id="state_reconstruction",
                    phase_index=1,
                    objective="Reconstruct incident state from prior session artifacts.",
                    expected_capability_ids=("episodic_recall", "retrieval_grounding"),
                    required_tools=("run_replay", "memory_retrieval"),
                    success_metrics=("recall_accuracy", "citation_coverage"),
                    max_turns=3,
                    metadata={},
                ),
                LongHorizonCheckpoint(
                    checkpoint_id="task_reprioritization",
                    phase_index=2,
                    objective="Reprioritize unresolved tasks using current risk posture.",
                    expected_capability_ids=("uncertainty_reasoning", "contingency_replanning"),
                    required_tools=("open_loop_register", "event_bus"),
                    success_metrics=("calibration", "recovery_quality"),
                    max_turns=4,
                    metadata={},
                ),
                LongHorizonCheckpoint(
                    checkpoint_id="evidence_complete_handoff",
                    phase_index=3,
                    objective="Deliver evidence-complete handoff with explicit open risks.",
                    expected_capability_ids=("preference_consistency", "tool_selection"),
                    required_tools=("status_summary", "reporter"),
                    success_metrics=("consistency", "completion_quality"),
                    max_turns=3,
                    metadata={},
                ),
            ),
            perturbations=(
                LongHorizonPerturbation(
                    perturbation_id="context_gap",
                    trigger_checkpoint_id="state_reconstruction",
                    perturbation_type="context_loss",
                    severity=0.65,
                    description="A subset of expected artifacts is missing from the initial load.",
                    expected_adaptations=("gap_flagging", "evidence_recovery_plan"),
                    metadata={},
                ),
            ),
            completion_metrics=("recall_accuracy", "consistency", "groundedness"),
            metadata={"track": "continuity"},
        ),
    )

    suite = LongHorizonScenarioSuite(
        suite_version="1.0.0",
        created_at=_utc_now_iso(),
        scenarios=tuple(sorted(scenarios, key=lambda item: item.scenario_id)),
        metadata={
            "program": "moonshot_capability",
            "phase": "P10-T3",
            "notes": "Baseline long-horizon mission scenario suite for benchmark expansion.",
        },
    )
    validate_long_horizon_scenario_suite(suite, taxonomy=taxonomy)
    return suite


def validate_long_horizon_scenario_suite(
    suite: LongHorizonScenarioSuite,
    *,
    taxonomy: BenchmarkTaxonomy | None = None,
) -> None:
    if not isinstance(suite, LongHorizonScenarioSuite):
        raise TypeError("suite must be LongHorizonScenarioSuite")

    if taxonomy is None:
        taxonomy = build_default_benchmark_taxonomy()
    validate_benchmark_taxonomy(taxonomy)

    _normalize_required(suite.suite_version, "suite_version")
    _parse_iso_timestamp(suite.created_at)

    if not suite.scenarios:
        raise LongHorizonScenarioError("suite must include at least one scenario")

    capability_ids = {capability.capability_id for capability in taxonomy.capabilities}

    scenario_ids: set[str] = set()
    has_long_horizon_tracking = False

    for scenario in suite.scenarios:
        normalized_scenario_id = _normalize_required(scenario.scenario_id, "scenario_id").lower()
        if normalized_scenario_id in scenario_ids:
            raise LongHorizonScenarioError(f"Duplicate scenario_id: {normalized_scenario_id}")
        scenario_ids.add(normalized_scenario_id)

        if scenario.primary_capability_id not in capability_ids:
            raise LongHorizonScenarioError(
                f"Scenario {scenario.scenario_id} references unknown primary capability {scenario.primary_capability_id}"
            )
        if scenario.primary_capability_id == "long_horizon_tracking":
            has_long_horizon_tracking = True

        if scenario.horizon_turns < 2:
            raise LongHorizonScenarioError(
                f"Scenario {scenario.scenario_id} must allow at least 2 turns"
            )

        if len(scenario.checkpoints) < 2:
            raise LongHorizonScenarioError(
                f"Scenario {scenario.scenario_id} must include at least 2 checkpoints"
            )

        if not scenario.completion_metrics:
            raise LongHorizonScenarioError(
                f"Scenario {scenario.scenario_id} must include completion_metrics"
            )

        checkpoint_ids: set[str] = set()
        expected_phase_index = 1
        turn_budget = 0

        for checkpoint in scenario.checkpoints:
            normalized_checkpoint_id = _normalize_required(checkpoint.checkpoint_id, "checkpoint_id").lower()
            if normalized_checkpoint_id in checkpoint_ids:
                raise LongHorizonScenarioError(
                    f"Scenario {scenario.scenario_id} has duplicate checkpoint {normalized_checkpoint_id}"
                )
            checkpoint_ids.add(normalized_checkpoint_id)

            if checkpoint.phase_index != expected_phase_index:
                raise LongHorizonScenarioError(
                    f"Scenario {scenario.scenario_id} checkpoint {normalized_checkpoint_id} has non-sequential phase_index"
                )
            expected_phase_index += 1

            if checkpoint.max_turns < 1:
                raise LongHorizonScenarioError(
                    f"Checkpoint {normalized_checkpoint_id} must allow at least 1 turn"
                )
            turn_budget += checkpoint.max_turns

            if not checkpoint.expected_capability_ids:
                raise LongHorizonScenarioError(
                    f"Checkpoint {normalized_checkpoint_id} must include expected_capability_ids"
                )
            for capability_id in checkpoint.expected_capability_ids:
                if capability_id not in capability_ids:
                    raise LongHorizonScenarioError(
                        f"Checkpoint {normalized_checkpoint_id} references unknown capability {capability_id}"
                    )

            if not checkpoint.success_metrics:
                raise LongHorizonScenarioError(
                    f"Checkpoint {normalized_checkpoint_id} must include success_metrics"
                )

        if scenario.horizon_turns < turn_budget:
            raise LongHorizonScenarioError(
                f"Scenario {scenario.scenario_id} horizon_turns is below checkpoint turn budget"
            )

        perturbation_ids: set[str] = set()
        for perturbation in scenario.perturbations:
            normalized_perturbation_id = _normalize_required(
                perturbation.perturbation_id,
                "perturbation_id",
            ).lower()
            if normalized_perturbation_id in perturbation_ids:
                raise LongHorizonScenarioError(
                    f"Scenario {scenario.scenario_id} has duplicate perturbation {normalized_perturbation_id}"
                )
            perturbation_ids.add(normalized_perturbation_id)

            trigger_checkpoint_id = _normalize_required(
                perturbation.trigger_checkpoint_id,
                "trigger_checkpoint_id",
            ).lower()
            if trigger_checkpoint_id not in checkpoint_ids:
                raise LongHorizonScenarioError(
                    f"Scenario {scenario.scenario_id} perturbation {normalized_perturbation_id} references unknown checkpoint {trigger_checkpoint_id}"
                )

            if perturbation.severity < 0 or perturbation.severity > 1:
                raise LongHorizonScenarioError(
                    f"Perturbation {normalized_perturbation_id} severity must be between 0 and 1"
                )

            _normalize_required(perturbation.perturbation_type, "perturbation_type")
            _normalize_required(perturbation.description, "perturbation.description")

            if not perturbation.expected_adaptations:
                raise LongHorizonScenarioError(
                    f"Perturbation {normalized_perturbation_id} must include expected_adaptations"
                )

    if not has_long_horizon_tracking:
        raise LongHorizonScenarioError(
            "suite must include at least one scenario with primary capability long_horizon_tracking"
        )


def _derive_scenario_weight(scenario: LongHorizonMissionScenario) -> float:
    base = 1.0
    base += 0.08 * len(scenario.checkpoints)
    base += 0.12 * len(scenario.perturbations)
    base += 0.02 * scenario.horizon_turns
    return round(min(base, 2.5), 6)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise LongHorizonScenarioError(f"{field_name} is required")
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
