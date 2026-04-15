"""Tests for P11-T10 failure-injection drills across critical services."""

from __future__ import annotations

import unittest

from runtime.orchestration import (
    FailureInjectionDrillRunner,
    FailureInjectionScenario,
    default_failure_injection_scenarios,
)


class FailureInjectionDrillRunnerTests(unittest.TestCase):
    def test_default_scenarios_cover_critical_services(self) -> None:
        scenarios = default_failure_injection_scenarios()

        services = {item.service_id for item in scenarios}
        self.assertEqual(
            services,
            {"orchestration", "memory", "configuration", "security", "release_pipeline"},
        )

    def test_run_drills_reports_contained_when_handlers_meet_targets(self) -> None:
        def handler(scenario: FailureInjectionScenario, _context: dict):
            return {
                "status": "contained",
                "recovered": True,
                "rollback_triggered": scenario.service_id == "release_pipeline",
                "detection_seconds": 2.0,
                "response_seconds": min(10.0, scenario.target_response_seconds),
                "detail": "contained",
            }

        runner = FailureInjectionDrillRunner(
            handlers={
                "orchestration": handler,
                "memory": handler,
                "configuration": handler,
                "security": handler,
                "release_pipeline": handler,
            }
        )

        report = runner.run_drills(default_failure_injection_scenarios())

        self.assertEqual(report.contained_count, 5)
        self.assertEqual(report.degraded_count, 0)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.readiness_score, 1.0)

    def test_missing_handler_marks_scenario_failed(self) -> None:
        def orchestration_handler(_scenario: FailureInjectionScenario, _context: dict):
            return {
                "status": "contained",
                "recovered": True,
                "detection_seconds": 1.0,
                "response_seconds": 5.0,
            }

        runner = FailureInjectionDrillRunner(
            handlers={"orchestration": orchestration_handler}
        )

        report = runner.run_drills(default_failure_injection_scenarios())

        self.assertGreater(report.failed_count, 0)
        self.assertTrue(any("No handler registered" in item.reason for item in report.results))

    def test_response_budget_breach_downgrades_contained_to_degraded(self) -> None:
        scenario = FailureInjectionScenario(
            scenario_id="drill-timeout",
            title="Timeout drill",
            service_id="orchestration",
            fault_type="timeout",
            severity="critical",
            target_response_seconds=10.0,
            expected_outcomes=("contained",),
            metadata={},
        )

        runner = FailureInjectionDrillRunner(
            handlers={
                "orchestration": lambda _scenario, _context: {
                    "status": "contained",
                    "recovered": True,
                    "detection_seconds": 1.0,
                    "response_seconds": 18.0,
                    "detail": "late but recovered",
                }
            }
        )

        report = runner.run_drills((scenario,))

        self.assertEqual(report.degraded_count, 1)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.results[0].status, "degraded")

    def test_report_manifest_is_deterministic(self) -> None:
        handler = lambda _scenario, _context: {
            "status": "contained",
            "recovered": True,
            "detection_seconds": 1.0,
            "response_seconds": 5.0,
        }
        runner = FailureInjectionDrillRunner(
            handlers={
                "orchestration": handler,
                "memory": handler,
                "configuration": handler,
                "security": handler,
                "release_pipeline": handler,
            }
        )

        report = runner.run_drills(default_failure_injection_scenarios())

        first = report.to_manifest()
        second = report.to_manifest()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
