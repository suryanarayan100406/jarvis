"""Tests for P11-T1 SLO and error-budget definitions."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.orchestration import (
    ErrorBudgetMonitor,
    SLOErrorBudgetError,
    SLOObservation,
    build_default_core_slo_catalog,
    validate_slo_catalog,
)


class SLOErrorBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_core_slo_catalog()
        self.monitor = ErrorBudgetMonitor()

    def test_default_catalog_is_valid_with_required_subsystem_coverage(self) -> None:
        validate_slo_catalog(self.catalog)

        subsystem_ids = {slo.subsystem_id for slo in self.catalog.slos}
        self.assertIn("orchestration", subsystem_ids)
        self.assertIn("planner", subsystem_ids)
        self.assertIn("executor", subsystem_ids)
        self.assertIn("memory", subsystem_ids)
        self.assertIn("policy", subsystem_ids)
        self.assertIn("security", subsystem_ids)

    def test_catalog_manifest_is_deterministic(self) -> None:
        first = self.catalog.to_manifest()
        second = self.catalog.to_manifest()
        self.assertEqual(first, second)

    def test_monitor_evaluates_warning_critical_and_breached_statuses(self) -> None:
        observations = []
        for slo in self.catalog.slos:
            if slo.slo_id == "memory_retrieval_grounded":
                observations.append(
                    SLOObservation(
                        slo_id=slo.slo_id,
                        total_events=10_000,
                        compliant_events=9_928,
                        elapsed_days=17,
                        metadata={"scenario": "warning"},
                    )
                )
            elif slo.slo_id == "executor_run_success":
                observations.append(
                    SLOObservation(
                        slo_id=slo.slo_id,
                        total_events=10_000,
                        compliant_events=9_973,
                        elapsed_days=10,
                        metadata={"scenario": "critical"},
                    )
                )
            elif slo.slo_id == "policy_decision_integrity":
                observations.append(
                    SLOObservation(
                        slo_id=slo.slo_id,
                        total_events=10_000,
                        compliant_events=9_980,
                        elapsed_days=20,
                        metadata={"scenario": "breached"},
                    )
                )
            else:
                observations.append(
                    SLOObservation(
                        slo_id=slo.slo_id,
                        total_events=10_000,
                        compliant_events=10_000,
                        elapsed_days=14,
                        metadata={"scenario": "healthy"},
                    )
                )

        report = self.monitor.evaluate_catalog(self.catalog, observations)

        self.assertEqual(report.evaluation_count, len(self.catalog.slos))
        self.assertGreaterEqual(report.warning_count, 1)
        self.assertGreaterEqual(report.critical_count, 1)
        self.assertGreaterEqual(report.breached_count, 1)

    def test_validation_rejects_duplicate_slo_ids(self) -> None:
        duplicate_slos = list(self.catalog.slos)
        duplicate_slos[1] = replace(duplicate_slos[1], slo_id=duplicate_slos[0].slo_id)
        invalid_catalog = replace(self.catalog, slos=tuple(duplicate_slos))

        with self.assertRaises(SLOErrorBudgetError):
            validate_slo_catalog(invalid_catalog)

    def test_monitor_rejects_missing_observations(self) -> None:
        observations = [
            SLOObservation(
                slo_id=self.catalog.slos[0].slo_id,
                total_events=100,
                compliant_events=100,
                elapsed_days=7,
                metadata={},
            )
        ]

        with self.assertRaises(SLOErrorBudgetError):
            self.monitor.evaluate_catalog(self.catalog, observations)

    def test_monitor_rejects_invalid_observation_counts(self) -> None:
        observations = []
        for slo in self.catalog.slos:
            compliant_events = 120 if slo.slo_id == self.catalog.slos[0].slo_id else 100
            observations.append(
                SLOObservation(
                    slo_id=slo.slo_id,
                    total_events=100,
                    compliant_events=compliant_events,
                    elapsed_days=7,
                    metadata={},
                )
            )

        with self.assertRaises(SLOErrorBudgetError):
            self.monitor.evaluate_catalog(self.catalog, observations)


if __name__ == "__main__":
    unittest.main()