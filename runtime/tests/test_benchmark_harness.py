"""Tests for P10-T2 benchmark harness runner reproducibility."""

from __future__ import annotations

import unittest

from runtime.moonshot import (
    BenchmarkHarnessError,
    BenchmarkHarnessRunner,
    BenchmarkScenarioDefinition,
    build_default_benchmark_taxonomy,
)


class DeterministicRandomEvaluator:
    def evaluate(self, scenario: BenchmarkScenarioDefinition, *, random_state) -> float:
        del scenario
        return random_state.random()


class MappingEvaluator:
    def __init__(self, mapping: dict[str, float]) -> None:
        self.mapping = dict(mapping)

    def evaluate(self, scenario: BenchmarkScenarioDefinition, *, random_state) -> float:
        del random_state
        return self.mapping[scenario.capability_id]


class InvalidEvaluator:
    def evaluate(self, scenario: BenchmarkScenarioDefinition, *, random_state) -> float:
        del scenario
        del random_state
        return 1.5


class BenchmarkHarnessRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = build_default_benchmark_taxonomy()

    def test_reproducible_digest_for_same_seed(self) -> None:
        scenarios = _full_coverage_scenarios(self.taxonomy)

        runner_a = BenchmarkHarnessRunner(self.taxonomy, seed=42)
        runner_b = BenchmarkHarnessRunner(self.taxonomy, seed=42)
        evaluator = DeterministicRandomEvaluator()

        result_a = runner_a.run_benchmark(scenarios, evaluator)
        result_b = runner_b.run_benchmark(scenarios, evaluator)

        self.assertEqual(result_a.deterministic_digest, result_b.deterministic_digest)
        self.assertAlmostEqual(result_a.overall_score, result_b.overall_score, places=12)
        self.assertEqual(
            [item.scenario_seed for item in result_a.scenario_results],
            [item.scenario_seed for item in result_b.scenario_results],
        )

    def test_digest_changes_for_different_seed(self) -> None:
        scenarios = _full_coverage_scenarios(self.taxonomy)
        evaluator = DeterministicRandomEvaluator()

        result_a = BenchmarkHarnessRunner(self.taxonomy, seed=1).run_benchmark(scenarios, evaluator)
        result_b = BenchmarkHarnessRunner(self.taxonomy, seed=99).run_benchmark(scenarios, evaluator)

        self.assertNotEqual(result_a.deterministic_digest, result_b.deterministic_digest)

    def test_rejects_unknown_capability_reference(self) -> None:
        scenarios = _full_coverage_scenarios(self.taxonomy)
        scenarios[0] = BenchmarkScenarioDefinition(
            scenario_id=scenarios[0].scenario_id,
            capability_id="missing_capability",
            difficulty_band_id=scenarios[0].difficulty_band_id,
            weight=scenarios[0].weight,
        )

        with self.assertRaises(BenchmarkHarnessError):
            BenchmarkHarnessRunner(self.taxonomy).run_benchmark(
                scenarios,
                DeterministicRandomEvaluator(),
            )

    def test_rejects_invalid_raw_score(self) -> None:
        scenarios = _full_coverage_scenarios(self.taxonomy)

        with self.assertRaises(BenchmarkHarnessError):
            BenchmarkHarnessRunner(self.taxonomy).run_benchmark(scenarios, InvalidEvaluator())

    def test_domain_and_overall_scores_are_weighted(self) -> None:
        scenarios = _full_coverage_scenarios(self.taxonomy)
        mapping = {
            "analogical_reasoning": 0.9,
            "causal_inference": 0.8,
            "uncertainty_reasoning": 0.7,
            "decomposition_planning": 0.6,
            "contingency_replanning": 0.5,
            "long_horizon_tracking": 0.4,
            "episodic_recall": 0.75,
            "preference_consistency": 0.65,
            "retrieval_grounding": 0.55,
            "tool_selection": 0.85,
            "schema_constrained_invocation": 0.8,
            "multi_tool_coordination": 0.7,
        }

        result = BenchmarkHarnessRunner(self.taxonomy, seed=7).run_benchmark(
            scenarios,
            MappingEvaluator(mapping),
        )

        self.assertEqual(result.scenario_count, len(scenarios))
        self.assertEqual(len(result.domain_scores), 4)
        self.assertGreaterEqual(result.overall_score, 0.0)
        self.assertLessEqual(result.overall_score, 1.0)

        weighted = sum(domain.weighted_score * domain.weight for domain in result.domain_scores)
        self.assertAlmostEqual(result.overall_score, weighted, places=12)

    def test_strict_coverage_can_be_relaxed(self) -> None:
        subset = [
            BenchmarkScenarioDefinition(
                scenario_id="reasoning-only",
                capability_id="analogical_reasoning",
                difficulty_band_id="baseline",
                weight=1.0,
            )
        ]

        runner = BenchmarkHarnessRunner(self.taxonomy, seed=3)

        with self.assertRaises(BenchmarkHarnessError):
            runner.run_benchmark(subset, DeterministicRandomEvaluator(), strict_coverage=True)

        result = runner.run_benchmark(subset, DeterministicRandomEvaluator(), strict_coverage=False)
        self.assertEqual(result.scenario_count, 1)
        self.assertEqual(len(result.domain_scores), 1)



def _full_coverage_scenarios(taxonomy) -> list[BenchmarkScenarioDefinition]:
    scenarios: list[BenchmarkScenarioDefinition] = []
    for index, capability in enumerate(sorted(taxonomy.capabilities, key=lambda item: item.capability_id), start=1):
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
