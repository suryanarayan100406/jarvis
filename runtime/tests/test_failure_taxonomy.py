"""Tests for P10-T9 failure taxonomy and root-cause labeling."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.moonshot import (
    BenchmarkHarnessRunner,
    BenchmarkScenarioDefinition,
    FailureRootCauseLabeler,
    FailureSignal,
    FailureTaxonomyError,
    SafetyRegressionGate,
    build_default_benchmark_taxonomy,
    build_default_failure_taxonomy,
    validate_failure_taxonomy,
)


class MappingEvaluator:
    def __init__(self, mapping: dict[str, float]) -> None:
        self.mapping = dict(mapping)

    def evaluate(self, scenario: BenchmarkScenarioDefinition, *, random_state) -> float:
        del random_state
        return self.mapping[scenario.capability_id]


class FailureTaxonomyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = build_default_failure_taxonomy()
        self.labeler = FailureRootCauseLabeler(self.taxonomy)

    def test_default_taxonomy_manifest_is_deterministic(self) -> None:
        first = self.taxonomy.to_manifest()
        second = self.taxonomy.to_manifest()

        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first["categories"]), 4)
        self.assertGreaterEqual(len(first["root_causes"]), 6)

    def test_signal_labeling_detects_regression_and_policy_issues(self) -> None:
        signals = [
            FailureSignal(
                signal_id="s-1",
                source_id="benchmark_overall",
                metric_id="overall_score",
                severity="high",
                description="overall regression score drop detected after model update",
                observed_value=0.51,
                expected_value=0.58,
                metadata={"change_type": "model"},
            ),
            FailureSignal(
                signal_id="s-2",
                source_id="safety_gate",
                metric_id="risk_tier_policy",
                severity="medium",
                description="policy threshold block triggered for risk_tier high",
                observed_value=None,
                expected_value=None,
                metadata={"rule": "policy.high"},
            ),
        ]

        report = self.labeler.label_signals(signals, max_labels=4, min_confidence=0.1)
        root_cause_ids = {label.root_cause_id for label in report.labels}

        self.assertIn("capability_score_regression", root_cause_ids)
        self.assertTrue(
            "safety_policy_misconfiguration" in root_cause_ids
            or "approval_workflow_breakdown" in root_cause_ids
        )
        self.assertEqual(report.signal_count, 2)
        self.assertEqual(len(report.unmatched_signal_ids), 0)

    def test_signal_labeling_returns_unmatched_when_no_keywords(self) -> None:
        signals = [
            FailureSignal(
                signal_id="s-unknown",
                source_id="mystery_source",
                metric_id="unknown_metric",
                severity="low",
                description="unexpected cosmic variance in isolated workload",
                observed_value=None,
                expected_value=None,
                metadata={"note": "no taxonomy match expected"},
            )
        ]

        report = self.labeler.label_signals(signals, min_confidence=0.2)
        self.assertEqual(len(report.labels), 0)
        self.assertEqual(report.unmatched_signal_ids, ("s-unknown",))

    def test_label_safety_regression_result(self) -> None:
        taxonomy = build_default_benchmark_taxonomy()
        runner = BenchmarkHarnessRunner(taxonomy, seed=7)
        scenarios = _full_coverage_scenarios(taxonomy)

        baseline_mapping = {
            capability.capability_id: 0.82
            for capability in taxonomy.capabilities
        }
        candidate_mapping = {
            capability_id: score - 0.08
            for capability_id, score in baseline_mapping.items()
        }

        baseline = runner.run_benchmark(scenarios, MappingEvaluator(baseline_mapping), run_id="baseline")
        candidate = runner.run_benchmark(scenarios, MappingEvaluator(candidate_mapping), run_id="candidate")

        gate_result = SafetyRegressionGate().evaluate_change(
            baseline_run=baseline,
            candidate_run=candidate,
            change_id="chg-1",
            change_type="model",
            risk_tier="medium",
        )

        report = self.labeler.label_safety_regression_result(gate_result)

        self.assertGreaterEqual(report.signal_count, 1)
        self.assertGreaterEqual(len(report.labels), 1)
        root_cause_ids = {label.root_cause_id for label in report.labels}
        self.assertTrue(
            "capability_score_regression" in root_cause_ids
            or "safety_policy_misconfiguration" in root_cause_ids
            or "benchmark_integrity_drift" in root_cause_ids
        )

    def test_validate_rejects_duplicate_root_cause_ids(self) -> None:
        duplicated = self.taxonomy.root_causes + (
            replace(self.taxonomy.root_causes[0], title="Duplicate Root Cause"),
        )
        invalid = replace(self.taxonomy, root_causes=duplicated)

        with self.assertRaises(FailureTaxonomyError):
            validate_failure_taxonomy(invalid)

    def test_max_labels_limits_output(self) -> None:
        signals = [
            FailureSignal(
                signal_id="s-many",
                source_id="safety_gate_integrity",
                metric_id="taxonomy_version",
                severity="critical",
                description="integrity regression policy approval rollback threshold violation",
                observed_value=None,
                expected_value=None,
                metadata={"strict_coverage": False},
            )
        ]

        report = self.labeler.label_signals(signals, max_labels=1, min_confidence=0.05)
        self.assertLessEqual(len(report.labels), 1)


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
