"""Adversarial robustness tests for uncertainty-heavy moonshot intelligence flows (P10-T11)."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.moonshot import (
    BenchmarkHarnessRunner,
    BenchmarkScenarioDefinition,
    FailureRootCauseLabeler,
    FailureSignal,
    QuarterlyGapReportGenerator,
    SafetyRegressionGate,
    build_default_benchmark_taxonomy,
    build_default_cross_domain_transfer_suite,
    build_default_long_horizon_scenario_suite,
)


class AdversarialUncertaintyEvaluator:
    def __init__(self, mapping: dict[str, float], *, jitter: float = 0.04) -> None:
        self.mapping = dict(mapping)
        self.jitter = float(jitter)

    def evaluate(self, scenario: BenchmarkScenarioDefinition, *, random_state) -> float:
        base = self.mapping[scenario.capability_id]
        jitter = random_state.uniform(-self.jitter, self.jitter)

        adversarial_penalty = 0.0
        prompt = (scenario.prompt or "").lower()
        if "uncertainty" in prompt:
            adversarial_penalty += 0.02

        perturbation_count = int(scenario.metadata.get("perturbation_count", 0))
        transfer_turns = int(scenario.metadata.get("transfer_turns", 0))
        adversarial_penalty += min(0.08, perturbation_count * 0.01)
        adversarial_penalty += min(0.05, transfer_turns * 0.002)

        score = base + jitter - adversarial_penalty
        return max(0.0, min(1.0, score))


class MappingEvaluator:
    def __init__(self, mapping: dict[str, float]) -> None:
        self.mapping = dict(mapping)

    def evaluate(self, scenario: BenchmarkScenarioDefinition, *, random_state) -> float:
        del random_state
        return self.mapping[scenario.capability_id]


class MoonshotAdversarialUncertaintyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = build_default_benchmark_taxonomy()
        self.capability_ids = [
            capability.capability_id
            for capability in sorted(self.taxonomy.capabilities, key=lambda item: item.capability_id)
        ]
        self.baseline_mapping = {capability_id: 0.82 for capability_id in self.capability_ids}

    def test_adversarial_uncertainty_runs_are_reproducible_for_fixed_seed(self) -> None:
        scenarios = self._moonshot_scenarios()
        evaluator = AdversarialUncertaintyEvaluator(self.baseline_mapping, jitter=0.05)

        result_a = BenchmarkHarnessRunner(self.taxonomy, seed=211).run_benchmark(
            scenarios,
            evaluator,
            run_id="adv-uncertainty-a",
        )
        result_b = BenchmarkHarnessRunner(self.taxonomy, seed=211).run_benchmark(
            scenarios,
            evaluator,
            run_id="adv-uncertainty-b",
        )

        self.assertEqual(result_a.deterministic_digest, result_b.deterministic_digest)
        self.assertAlmostEqual(result_a.overall_score, result_b.overall_score, places=12)

    def test_safety_gate_blocks_hidden_uncertainty_capability_regression(self) -> None:
        scenarios = self._moonshot_scenarios()
        runner = BenchmarkHarnessRunner(self.taxonomy, seed=307)

        candidate_mapping = {capability_id: 0.84 for capability_id in self.capability_ids}
        candidate_mapping["uncertainty_reasoning"] = 0.70
        candidate_mapping["causal_inference"] = 0.79

        baseline_run = runner.run_benchmark(
            scenarios,
            MappingEvaluator(self.baseline_mapping),
            run_id="adv-baseline",
        )
        candidate_run = runner.run_benchmark(
            scenarios,
            MappingEvaluator(candidate_mapping),
            run_id="adv-candidate",
        )

        gate_result = SafetyRegressionGate().evaluate_change(
            baseline_run=baseline_run,
            candidate_run=candidate_run,
            change_id="chg-uncertainty-hide",
            change_type="model",
            risk_tier="high",
        )

        self.assertEqual(gate_result.decision, "block")
        capability_references = {
            violation.reference_id
            for violation in gate_result.violations
            if violation.level == "capability"
        }
        self.assertIn("uncertainty_reasoning", capability_references)

    def test_failure_labeling_handles_noisy_signals_without_false_matches(self) -> None:
        labeler = FailureRootCauseLabeler()
        report = labeler.label_signals(
            [
                FailureSignal(
                    signal_id="uncertain-regression",
                    source_id="benchmark_reasoning",
                    metric_id="uncertainty_reasoning",
                    severity="high",
                    description="regression score delta observed under uncertain context",
                    observed_value=0.61,
                    expected_value=0.79,
                    metadata={"channel": "moonshot_eval"},
                ),
                FailureSignal(
                    signal_id="policy-block",
                    source_id="safety_gate",
                    metric_id="risk_tier_policy",
                    severity="critical",
                    description="policy threshold block triggered during approval rollback",
                    observed_value=None,
                    expected_value=None,
                    metadata={"rule": "policy.high"},
                ),
                FailureSignal(
                    signal_id="noise-01",
                    source_id="telemetry",
                    metric_id="entropy_flux",
                    severity="low",
                    description="fractal shimmer anomaly in packet stream",
                    observed_value=None,
                    expected_value=None,
                    metadata={"trace": "n/a"},
                ),
            ],
            max_labels=5,
            min_confidence=0.1,
        )

        root_cause_ids = {label.root_cause_id for label in report.labels}
        self.assertIn("capability_score_regression", root_cause_ids)
        self.assertTrue(
            "safety_policy_misconfiguration" in root_cause_ids
            or "approval_workflow_breakdown" in root_cause_ids
        )
        self.assertIn("noise-01", report.unmatched_signal_ids)

    def test_quarterly_gap_report_escalates_risk_for_high_severity_failure_labels(self) -> None:
        runs = self._build_runs(base_score=0.88, increments=[0.0, 0.01, 0.01])
        failure_report = FailureRootCauseLabeler().label_signals(
            [
                FailureSignal(
                    signal_id="gate-crit-1",
                    source_id="safety_gate",
                    metric_id="policy_threshold",
                    severity="critical",
                    description="policy threshold block deny after approval workflow regression",
                    observed_value=None,
                    expected_value=None,
                    metadata={"risk_tier": "high"},
                )
            ],
            min_confidence=0.05,
        )

        report = QuarterlyGapReportGenerator(window_size=6).generate_report(
            runs,
            quarter_id="2026-Q1",
            failure_report=failure_report,
        )

        self.assertEqual(report.overall_status, "met")
        self.assertEqual(report.risk_level, "elevated")
        self.assertTrue(
            any(recommendation.source == "failure_taxonomy" for recommendation in report.recommendations)
        )

    def test_quarterly_gap_report_sorts_out_of_order_run_inputs(self) -> None:
        runs = self._build_runs(base_score=0.78, increments=[0.0, 0.01, 0.02])
        shuffled_runs = [
            replace(runs[0], completed_at="2026-04-01T00:00:00Z"),
            replace(runs[1], completed_at="2026-01-01T00:00:00Z"),
            replace(runs[2], completed_at="2026-07-01T00:00:00Z"),
        ]

        report = QuarterlyGapReportGenerator(window_size=3).generate_report(
            shuffled_runs,
            quarter_id="2026-Q3",
        )

        self.assertEqual(report.run_count, 3)
        self.assertEqual(report.baseline_run_id, "adv-run-02")
        self.assertEqual(report.latest_run_id, "adv-run-03")

    def test_uncertainty_coverage_is_present_in_moonshot_scenario_suites(self) -> None:
        long_horizon_suite = build_default_long_horizon_scenario_suite(self.taxonomy)
        cross_domain_suite = build_default_cross_domain_transfer_suite(self.taxonomy)

        perturbation_types = {
            perturbation.perturbation_type
            for scenario in long_horizon_suite.scenarios
            for perturbation in scenario.perturbations
        }
        self.assertTrue({"context_loss", "data_quality"}.intersection(perturbation_types))

        uncertainty_targets = {
            checkpoint.target_capability_id
            for scenario in cross_domain_suite.scenarios
            for checkpoint in scenario.checkpoints
        }
        self.assertIn("uncertainty_reasoning", uncertainty_targets)

        long_horizon_bench = long_horizon_suite.to_benchmark_scenarios(default_difficulty_band="frontier")
        cross_domain_bench = cross_domain_suite.to_benchmark_scenarios(default_difficulty_band="advanced")

        self.assertTrue(all(item.metadata["perturbation_count"] >= 1 for item in long_horizon_bench))
        self.assertTrue(all(item.metadata["transfer_turns"] >= 10 for item in cross_domain_bench))

    def _moonshot_scenarios(self) -> list[BenchmarkScenarioDefinition]:
        scenarios = _full_coverage_scenarios(self.taxonomy)
        scenarios.extend(
            build_default_long_horizon_scenario_suite(self.taxonomy).to_benchmark_scenarios(
                default_difficulty_band="advanced"
            )
        )
        scenarios.extend(
            build_default_cross_domain_transfer_suite(self.taxonomy).to_benchmark_scenarios(
                default_difficulty_band="frontier"
            )
        )
        return scenarios

    def _build_runs(self, *, base_score: float, increments: list[float]):
        runner = BenchmarkHarnessRunner(self.taxonomy, seed=317)
        scenarios = self._moonshot_scenarios()

        runs = []
        for index, increment in enumerate(increments, start=1):
            mapping = {
                capability_id: max(0.0, min(1.0, base_score + increment))
                for capability_id in self.capability_ids
            }
            runs.append(
                runner.run_benchmark(
                    scenarios,
                    MappingEvaluator(mapping),
                    run_id=f"adv-run-{index:02d}",
                    metadata={"index": index},
                )
            )
        return runs


def _full_coverage_scenarios(taxonomy) -> list[BenchmarkScenarioDefinition]:
    scenarios: list[BenchmarkScenarioDefinition] = []
    for index, capability in enumerate(
        sorted(taxonomy.capabilities, key=lambda item: item.capability_id),
        start=1,
    ):
        band = "frontier" if index % 4 == 0 else "advanced" if index % 3 == 0 else "baseline"
        scenarios.append(
            BenchmarkScenarioDefinition(
                scenario_id=f"scenario-{index:02d}-{capability.capability_id}",
                capability_id=capability.capability_id,
                difficulty_band_id=band,
                weight=1.0,
                prompt=f"Evaluate {capability.capability_id}",
            )
        )
    return scenarios


if __name__ == "__main__":
    unittest.main()