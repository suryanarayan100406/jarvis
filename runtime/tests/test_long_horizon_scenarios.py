"""Tests for P10-T3 long-horizon mission scenario definitions."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.moonshot import (
    LongHorizonScenarioError,
    build_default_benchmark_taxonomy,
    build_default_long_horizon_scenario_suite,
    validate_long_horizon_scenario_suite,
)


class LongHorizonScenarioSuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = build_default_benchmark_taxonomy()

    def test_default_suite_is_valid_and_has_expected_coverage(self) -> None:
        suite = build_default_long_horizon_scenario_suite(self.taxonomy)

        validate_long_horizon_scenario_suite(suite, taxonomy=self.taxonomy)
        self.assertGreaterEqual(len(suite.scenarios), 4)

        primary_capability_ids = {item.primary_capability_id for item in suite.scenarios}
        self.assertIn("long_horizon_tracking", primary_capability_ids)
        self.assertIn("contingency_replanning", primary_capability_ids)
        self.assertIn("multi_tool_coordination", primary_capability_ids)

        for scenario in suite.scenarios:
            self.assertGreaterEqual(len(scenario.checkpoints), 2)
            self.assertGreaterEqual(scenario.horizon_turns, 2)

    def test_to_benchmark_scenarios_is_reproducible_and_sorted(self) -> None:
        suite = build_default_long_horizon_scenario_suite(self.taxonomy)

        first = suite.to_benchmark_scenarios(default_difficulty_band="advanced")
        second = suite.to_benchmark_scenarios(default_difficulty_band="advanced")

        self.assertEqual(first, second)

        scenario_ids = [scenario.scenario_id for scenario in first]
        self.assertEqual(scenario_ids, sorted(scenario_ids))

        for scenario in first:
            self.assertTrue(scenario.scenario_id.startswith("lh-"))
            self.assertGreater(scenario.weight, 1.0)
            self.assertLessEqual(scenario.weight, 2.5)

    def test_get_and_list_scenarios_helpers(self) -> None:
        suite = build_default_long_horizon_scenario_suite(self.taxonomy)

        expected = suite.scenarios[0]
        loaded = suite.get_scenario(expected.scenario_id)

        self.assertEqual(loaded, expected)

        filtered = suite.list_scenarios(primary_capability_id="long_horizon_tracking")
        self.assertGreaterEqual(len(filtered), 1)
        self.assertTrue(all(item.primary_capability_id == "long_horizon_tracking" for item in filtered))

    def test_validation_rejects_unknown_primary_capability(self) -> None:
        suite = build_default_long_horizon_scenario_suite(self.taxonomy)

        broken = replace(suite.scenarios[0], primary_capability_id="missing_capability")
        invalid_suite = replace(suite, scenarios=(broken, *suite.scenarios[1:]))

        with self.assertRaises(LongHorizonScenarioError):
            validate_long_horizon_scenario_suite(invalid_suite, taxonomy=self.taxonomy)

    def test_validation_rejects_bad_perturbation_checkpoint_reference(self) -> None:
        suite = build_default_long_horizon_scenario_suite(self.taxonomy)

        scenario = suite.scenarios[0]
        broken_perturbation = replace(
            scenario.perturbations[0],
            trigger_checkpoint_id="missing_checkpoint",
        )
        broken_scenario = replace(
            scenario,
            perturbations=(broken_perturbation, *scenario.perturbations[1:]),
        )
        invalid_suite = replace(suite, scenarios=(broken_scenario, *suite.scenarios[1:]))

        with self.assertRaises(LongHorizonScenarioError):
            validate_long_horizon_scenario_suite(invalid_suite, taxonomy=self.taxonomy)

    def test_validation_rejects_turn_budget_underflow(self) -> None:
        suite = build_default_long_horizon_scenario_suite(self.taxonomy)

        scenario = suite.scenarios[0]
        broken_scenario = replace(scenario, horizon_turns=1)
        invalid_suite = replace(suite, scenarios=(broken_scenario, *suite.scenarios[1:]))

        with self.assertRaises(LongHorizonScenarioError):
            validate_long_horizon_scenario_suite(invalid_suite, taxonomy=self.taxonomy)


if __name__ == "__main__":
    unittest.main()
