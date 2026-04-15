"""Tests for P10-T7 safety regression gate controls."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.moonshot import (
    BenchmarkHarnessRunner,
    BenchmarkScenarioDefinition,
    SafetyRegressionGate,
    SafetyRegressionGateError,
    SafetyRegressionPolicy,
    build_default_benchmark_taxonomy,
    validate_safety_regression_policy,
)


class MappingEvaluator:
    def __init__(self, mapping: dict[str, float]) -> None:
        self.mapping = dict(mapping)

    def evaluate(self, scenario: BenchmarkScenarioDefinition, *, random_state) -> float:
        del random_state
        return self.mapping[scenario.capability_id]


class SafetyRegressionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = build_default_benchmark_taxonomy()
        self.runner = BenchmarkHarnessRunner(self.taxonomy, seed=33)
        self.scenarios = _full_coverage_scenarios(self.taxonomy)
        self.gate = SafetyRegressionGate()

        self.baseline_mapping = {
            capability.capability_id: 0.82
            for capability in self.taxonomy.capabilities
        }

    def test_gate_allows_small_high_risk_regression_within_threshold(self) -> None:
        baseline = self._run(self.baseline_mapping)
        candidate_mapping = {
            capability_id: score - 0.015
            for capability_id, score in self.baseline_mapping.items()
        }
        candidate = self._run(candidate_mapping)

        result = self.gate.evaluate_change(
            baseline_run=baseline,
            candidate_run=candidate,
            change_id="chg-small-high",
            change_type="model",
            risk_tier="high",
        )

        self.assertEqual(result.decision, "allow")
        self.assertEqual(len(result.violations), 0)

    def test_gate_blocks_overall_regression_exceeding_threshold(self) -> None:
        baseline = self._run(self.baseline_mapping)
        candidate_mapping = {
            capability_id: score - 0.08
            for capability_id, score in self.baseline_mapping.items()
        }
        candidate = self._run(candidate_mapping)

        result = self.gate.evaluate_change(
            baseline_run=baseline,
            candidate_run=candidate,
            change_id="chg-overall-drop",
            change_type="model",
            risk_tier="medium",
        )

        self.assertEqual(result.decision, "block")
        levels = {violation.level for violation in result.violations}
        self.assertIn("overall", levels)

    def test_gate_blocks_domain_specific_regression(self) -> None:
        baseline = self._run(self.baseline_mapping)

        candidate_mapping = dict(self.baseline_mapping)
        for capability_id in ("analogical_reasoning", "causal_inference", "uncertainty_reasoning"):
            candidate_mapping[capability_id] = 0.70

        candidate = self._run(candidate_mapping)
        result = self.gate.evaluate_change(
            baseline_run=baseline,
            candidate_run=candidate,
            change_id="chg-domain-reasoning",
            change_type="policy",
            risk_tier="low",
        )

        self.assertEqual(result.decision, "block")
        domain_violations = [
            violation
            for violation in result.violations
            if violation.level == "domain"
        ]
        self.assertTrue(any(item.reference_id == "reasoning" for item in domain_violations))

    def test_gate_blocks_integrity_mismatch(self) -> None:
        baseline = self._run(self.baseline_mapping)
        candidate = self._run(self.baseline_mapping)
        candidate = replace(candidate, taxonomy_version="2.0.0")

        result = self.gate.evaluate_change(
            baseline_run=baseline,
            candidate_run=candidate,
            change_id="chg-integrity",
            change_type="model",
            risk_tier="low",
        )

        self.assertEqual(result.decision, "block")
        integrity_references = {
            violation.reference_id
            for violation in result.violations
            if violation.level == "integrity"
        }
        self.assertIn("taxonomy_version", integrity_references)

    def test_gate_requires_strict_coverage_runs(self) -> None:
        baseline = self._run(self.baseline_mapping)

        subset_scenarios = [
            scenario
            for scenario in self.scenarios
            if scenario.capability_id != "analogical_reasoning"
        ]
        candidate = self.runner.run_benchmark(
            subset_scenarios,
            MappingEvaluator(self.baseline_mapping),
            strict_coverage=False,
        )

        result = self.gate.evaluate_change(
            baseline_run=baseline,
            candidate_run=candidate,
            change_id="chg-non-strict",
            change_type="policy",
            risk_tier="medium",
        )

        self.assertEqual(result.decision, "block")
        self.assertTrue(any(violation.reference_id == "strict_coverage" for violation in result.violations))

    def test_high_risk_is_stricter_than_low_risk(self) -> None:
        baseline = self._run(self.baseline_mapping)
        candidate_mapping = {
            capability_id: score - 0.03
            for capability_id, score in self.baseline_mapping.items()
        }
        candidate = self._run(candidate_mapping)

        low_risk_result = self.gate.evaluate_change(
            baseline_run=baseline,
            candidate_run=candidate,
            change_id="chg-low-risk",
            change_type="model",
            risk_tier="low",
        )
        high_risk_result = self.gate.evaluate_change(
            baseline_run=baseline,
            candidate_run=candidate,
            change_id="chg-high-risk",
            change_type="model",
            risk_tier="high",
        )

        self.assertEqual(low_risk_result.decision, "allow")
        self.assertEqual(high_risk_result.decision, "block")

    def test_policy_validation_rejects_missing_rule_matrix_entry(self) -> None:
        policy = self.gate.policy
        invalid_policy = SafetyRegressionPolicy(
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            rules=tuple(
                rule
                for rule in policy.rules
                if not (rule.change_type == "policy" and rule.risk_tier == "critical")
            ),
            metadata=dict(policy.metadata),
        )

        with self.assertRaises(SafetyRegressionGateError):
            validate_safety_regression_policy(invalid_policy)

    def test_manifest_is_deterministic_for_same_result(self) -> None:
        baseline = self._run(self.baseline_mapping)
        candidate = self._run(self.baseline_mapping)

        result = self.gate.evaluate_change(
            baseline_run=baseline,
            candidate_run=candidate,
            change_id="chg-manifest",
            change_type="model",
            risk_tier="low",
        )

        first = result.to_manifest()
        second = result.to_manifest()
        self.assertEqual(first, second)

    def _run(self, mapping: dict[str, float]):
        return self.runner.run_benchmark(self.scenarios, MappingEvaluator(mapping))


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
