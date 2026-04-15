"""Tests for P10-T8 capability trend dashboard with confidence intervals."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.moonshot import (
    BenchmarkHarnessRunner,
    BenchmarkScenarioDefinition,
    CapabilityTrendDashboardBuilder,
    CapabilityTrendError,
    build_default_benchmark_taxonomy,
)


class MappingEvaluator:
    def __init__(self, mapping: dict[str, float]) -> None:
        self.mapping = dict(mapping)

    def evaluate(self, scenario: BenchmarkScenarioDefinition, *, random_state) -> float:
        del random_state
        return self.mapping[scenario.capability_id]


class CapabilityTrendDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = build_default_benchmark_taxonomy()
        self.runner = BenchmarkHarnessRunner(self.taxonomy, seed=17)
        self.scenarios = _full_coverage_scenarios(self.taxonomy)

    def test_dashboard_builds_with_confidence_intervals(self) -> None:
        runs = self._build_runs(base_score=0.70, increments=[0.00, 0.01, 0.02, 0.03])

        dashboard = CapabilityTrendDashboardBuilder(window_size=8, confidence_z=1.96).build_dashboard(runs)

        self.assertEqual(dashboard.run_count, 4)
        self.assertEqual(len(dashboard.domain_summaries), 4)
        self.assertEqual(len(dashboard.capability_summaries), 12)
        self.assertEqual(dashboard.overall_direction, "improving")
        self.assertEqual(dashboard.overall_confidence_interval.sample_count, 4)
        self.assertLessEqual(
            dashboard.overall_confidence_interval.lower_bound,
            dashboard.overall_confidence_interval.mean_score,
        )
        self.assertGreaterEqual(
            dashboard.overall_confidence_interval.upper_bound,
            dashboard.overall_confidence_interval.mean_score,
        )
        self.assertIn("Capability Trend Dashboard", dashboard.markdown)

    def test_window_size_limits_runs_used(self) -> None:
        runs = self._build_runs(base_score=0.66, increments=[0.00, 0.01, 0.02, 0.03, 0.04])

        dashboard = CapabilityTrendDashboardBuilder(window_size=3).build_dashboard(runs)

        self.assertEqual(dashboard.run_count, 3)
        self.assertEqual(dashboard.baseline_run_id, runs[-3].run_id)
        self.assertEqual(dashboard.latest_run_id, runs[-1].run_id)

    def test_manifest_is_deterministic(self) -> None:
        runs = self._build_runs(base_score=0.71, increments=[0.00, 0.01, 0.01])

        dashboard = CapabilityTrendDashboardBuilder(window_size=5).build_dashboard(runs)
        first = dashboard.to_manifest()
        second = dashboard.to_manifest()

        self.assertEqual(first, second)

    def test_single_run_has_zero_margin_interval(self) -> None:
        runs = self._build_runs(base_score=0.73, increments=[0.0])

        dashboard = CapabilityTrendDashboardBuilder(window_size=1).build_dashboard(runs)
        interval = dashboard.overall_confidence_interval

        self.assertEqual(interval.sample_count, 1)
        self.assertEqual(interval.margin_of_error, 0.0)
        self.assertEqual(interval.lower_bound, interval.mean_score)
        self.assertEqual(interval.upper_bound, interval.mean_score)

    def test_mixed_taxonomy_version_is_rejected(self) -> None:
        runs = self._build_runs(base_score=0.68, increments=[0.00, 0.01])
        invalid_runs = [replace(runs[0], taxonomy_version="2.0.0"), runs[1]]

        with self.assertRaises(CapabilityTrendError):
            CapabilityTrendDashboardBuilder().build_dashboard(invalid_runs)

    def test_non_strict_coverage_is_rejected(self) -> None:
        runs = self._build_runs(base_score=0.68, increments=[0.00, 0.01])
        invalid_runs = [replace(runs[0], strict_coverage=False), runs[1]]

        with self.assertRaises(CapabilityTrendError):
            CapabilityTrendDashboardBuilder().build_dashboard(invalid_runs)

    def test_declining_direction_is_detected(self) -> None:
        runs = self._build_runs(base_score=0.78, increments=[0.00, -0.01, -0.02])

        dashboard = CapabilityTrendDashboardBuilder(window_size=4).build_dashboard(runs)

        self.assertEqual(dashboard.overall_direction, "declining")
        self.assertLess(dashboard.overall_delta, 0)

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
            result = self.runner.run_benchmark(
                self.scenarios,
                MappingEvaluator(mapping),
                run_id=f"trend-run-{index:02d}",
                metadata={"index": index},
            )
            runs.append(result)

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
