"""Tests for P10-T4 cross-domain transfer evaluation suite."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.moonshot import (
    CrossDomainTransferError,
    build_default_benchmark_taxonomy,
    build_default_cross_domain_transfer_suite,
    validate_cross_domain_transfer_suite,
)


class CrossDomainTransferSuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = build_default_benchmark_taxonomy()

    def test_default_suite_is_valid_with_cross_domain_coverage(self) -> None:
        suite = build_default_cross_domain_transfer_suite(self.taxonomy)

        validate_cross_domain_transfer_suite(suite, taxonomy=self.taxonomy)
        self.assertGreaterEqual(len(suite.scenarios), 4)

        domain_pairs = {(item.source_domain_id, item.target_domain_id) for item in suite.scenarios}
        self.assertGreaterEqual(len(domain_pairs), 4)

        for scenario in suite.scenarios:
            self.assertNotEqual(scenario.source_domain_id, scenario.target_domain_id)
            self.assertGreaterEqual(len(scenario.checkpoints), 2)

    def test_to_benchmark_scenarios_is_deterministic(self) -> None:
        suite = build_default_cross_domain_transfer_suite(self.taxonomy)

        first = suite.to_benchmark_scenarios(default_difficulty_band="frontier")
        second = suite.to_benchmark_scenarios(default_difficulty_band="frontier")

        self.assertEqual(first, second)

        scenario_ids = [item.scenario_id for item in first]
        self.assertEqual(scenario_ids, sorted(scenario_ids))
        self.assertTrue(all(item.scenario_id.startswith("xd-") for item in first))
        self.assertTrue(all(item.capability_id in {cap.capability_id for cap in self.taxonomy.capabilities} for item in first))

    def test_list_scenarios_supports_source_and_target_filters(self) -> None:
        suite = build_default_cross_domain_transfer_suite(self.taxonomy)

        planning_to_tool_use = suite.list_scenarios(source_domain_id="planning", target_domain_id="tool_use")

        self.assertEqual(len(planning_to_tool_use), 1)
        self.assertEqual(planning_to_tool_use[0].scenario_id, "decomposition_to_orchestration_bridge")

    def test_validation_rejects_same_source_and_target_domain(self) -> None:
        suite = build_default_cross_domain_transfer_suite(self.taxonomy)
        scenario = suite.scenarios[0]

        invalid_scenario = replace(
            scenario,
            target_domain_id=scenario.source_domain_id,
        )
        invalid_suite = replace(suite, scenarios=(invalid_scenario, *suite.scenarios[1:]))

        with self.assertRaises(CrossDomainTransferError):
            validate_cross_domain_transfer_suite(invalid_suite, taxonomy=self.taxonomy)

    def test_validation_rejects_checkpoint_capability_domain_mismatch(self) -> None:
        suite = build_default_cross_domain_transfer_suite(self.taxonomy)
        scenario = suite.scenarios[0]

        broken_checkpoint = replace(
            scenario.checkpoints[0],
            source_capability_id="multi_tool_coordination",
        )
        broken_scenario = replace(
            scenario,
            checkpoints=(broken_checkpoint, *scenario.checkpoints[1:]),
        )
        invalid_suite = replace(suite, scenarios=(broken_scenario, *suite.scenarios[1:]))

        with self.assertRaises(CrossDomainTransferError):
            validate_cross_domain_transfer_suite(invalid_suite, taxonomy=self.taxonomy)

    def test_validation_rejects_insufficient_domain_pair_coverage(self) -> None:
        suite = build_default_cross_domain_transfer_suite(self.taxonomy)

        reduced_scenarios = tuple(suite.scenarios[:3])
        invalid_suite = replace(suite, scenarios=reduced_scenarios)

        with self.assertRaises(CrossDomainTransferError):
            validate_cross_domain_transfer_suite(invalid_suite, taxonomy=self.taxonomy)


if __name__ == "__main__":
    unittest.main()
