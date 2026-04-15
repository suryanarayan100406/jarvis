"""Tests for P10-T10 quarterly moonshot gap-report generation."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.moonshot import (
    BenchmarkHarnessRunner,
    BenchmarkScenarioDefinition,
    FailureRootCauseLabeler,
    FailureSignal,
    QuarterlyGapReportError,
    QuarterlyGapReportGenerator,
    build_default_benchmark_taxonomy,
    build_default_moonshot_target_profile,
)


class MappingEvaluator:
    def __init__(self, mapping: dict[str, float]) -> None:
        self.mapping = dict(mapping)

    def evaluate(self, scenario: BenchmarkScenarioDefinition, *, random_state) -> float:
        del random_state
        return self.mapping[scenario.capability_id]


class QuarterlyGapReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = build_default_benchmark_taxonomy()
        self.runner = BenchmarkHarnessRunner(self.taxonomy, seed=29)
        self.scenarios = _full_coverage_scenarios(self.taxonomy)

    def test_report_builds_against_default_targets(self) -> None:
        runs = self._build_runs(base_score=0.72, increments=[0.00, 0.03, 0.06])

        report = QuarterlyGapReportGenerator(window_size=8).generate_report(
            runs,
            quarter_id="2025-Q2",
        )

        self.assertEqual(report.quarter_id, "2025-Q2")
        self.assertEqual(report.run_count, 3)
        self.assertEqual(len(report.domain_gaps), 4)
        self.assertEqual(len(report.capability_gaps), 12)
        self.assertIn("Quarterly Moonshot Gap Report", report.markdown)
        self.assertEqual(report.overall_status, "off_track")
        self.assertEqual(report.risk_level, "elevated")

    def test_window_size_limits_runs_used(self) -> None:
        runs = self._build_runs(base_score=0.71, increments=[0.00, 0.01, 0.02, 0.03, 0.04])

        report = QuarterlyGapReportGenerator(window_size=3).generate_report(
            runs,
            quarter_id="2025-Q3",
        )

        self.assertEqual(report.run_count, 3)
        self.assertEqual(report.baseline_run_id, runs[-3].run_id)
        self.assertEqual(report.latest_run_id, runs[-1].run_id)

    def test_manifest_is_deterministic(self) -> None:
        runs = self._build_runs(base_score=0.75, increments=[0.00, 0.01, 0.01])

        report = QuarterlyGapReportGenerator().generate_report(
            runs,
            quarter_id="2025-Q4",
        )
        first = report.to_manifest()
        second = report.to_manifest()

        self.assertEqual(first, second)

    def test_rejects_mixed_taxonomy_versions(self) -> None:
        runs = self._build_runs(base_score=0.74, increments=[0.00, 0.02])
        invalid_runs = [replace(runs[0], taxonomy_version="2.0.0"), runs[1]]

        with self.assertRaises(QuarterlyGapReportError):
            QuarterlyGapReportGenerator().generate_report(invalid_runs, quarter_id="2025-Q1")

    def test_rejects_missing_capability_targets(self) -> None:
        runs = self._build_runs(base_score=0.76, increments=[0.00, 0.02])
        targets = build_default_moonshot_target_profile(runs[-1], quarter_id="2025-Q1")
        invalid_targets = replace(
            targets,
            capability_targets=targets.capability_targets[:-1],
        )

        with self.assertRaises(QuarterlyGapReportError):
            QuarterlyGapReportGenerator().generate_report(
                runs,
                quarter_id="2025-Q1",
                targets=invalid_targets,
            )

    def test_failure_labels_drive_recommendation_sources(self) -> None:
        runs = self._build_runs(base_score=0.73, increments=[0.00, 0.02, 0.03])

        labeler = FailureRootCauseLabeler()
        failure_report = labeler.label_signals(
            [
                FailureSignal(
                    signal_id="signal-1",
                    source_id="safety_gate",
                    metric_id="policy_threshold",
                    severity="high",
                    description="policy threshold regression block triggered for risk_tier high",
                    observed_value=None,
                    expected_value=None,
                    metadata={"rule": "policy.high"},
                )
            ],
            min_confidence=0.05,
        )

        report = QuarterlyGapReportGenerator().generate_report(
            runs,
            quarter_id="2025-Q1",
            failure_report=failure_report,
        )

        self.assertTrue(
            any(recommendation.source == "failure_taxonomy" for recommendation in report.recommendations)
        )

    def _build_runs(self, *, base_score: float, increments: list[float]):
        runs = []
        capability_ids = [
            capability.capability_id
            for capability in sorted(self.taxonomy.capabilities, key=lambda item: item.capability_id)
        ]

        for index, increment in enumerate(increments, start=1):
            mapping = {
                capability_id: max(0.0, min(1.0, base_score + increment))
                for capability_id in capability_ids
            }
            run = self.runner.run_benchmark(
                self.scenarios,
                MappingEvaluator(mapping),
                run_id=f"gap-run-{index:02d}",
                metadata={"index": index},
            )
            runs.append(run)

        return runs


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