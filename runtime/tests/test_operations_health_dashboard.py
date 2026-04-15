"""Tests for P11-T2 operations health dashboard generation."""

from __future__ import annotations

import unittest

from runtime.orchestration import (
    OperationalHealthMetric,
    OperationsHealthDashboardBuilder,
    OperationsHealthDashboardError,
)


class OperationsHealthDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = OperationsHealthDashboardBuilder()

    def test_dashboard_builds_with_required_domain_coverage(self) -> None:
        dashboard = self.builder.build_dashboard(
            _baseline_metrics(),
            window_id="2026-W15",
        )

        self.assertEqual(dashboard.window_id, "2026-W15")
        self.assertEqual(len(dashboard.domain_snapshots), 3)
        self.assertEqual(dashboard.overall_status, "healthy")
        self.assertIn("Operations Health Dashboard", dashboard.markdown)

    def test_autonomy_warning_propagates_to_overall_status(self) -> None:
        domain_metrics = _baseline_metrics()
        domain_metrics["autonomy"][0] = OperationalHealthMetric(
            metric_id="autonomous_success_ratio",
            value=0.84,
            target=0.99,
            direction="higher_is_better",
            warning_floor=0.9,
            critical_floor=0.75,
            weight=1.0,
            metadata={"unit": "ratio"},
        )

        dashboard = self.builder.build_dashboard(
            domain_metrics,
            window_id="2026-W16",
        )

        autonomy = _domain_snapshot(dashboard, "autonomy")
        self.assertEqual(autonomy.status, "warning")
        self.assertEqual(dashboard.overall_status, "warning")

    def test_security_critical_metric_sets_critical_overall_status(self) -> None:
        domain_metrics = _baseline_metrics()
        domain_metrics["security"][0] = OperationalHealthMetric(
            metric_id="guardrail_enforcement_ratio",
            value=0.60,
            target=0.999,
            direction="higher_is_better",
            warning_floor=0.9,
            critical_floor=0.75,
            weight=1.0,
            metadata={"unit": "ratio"},
        )

        dashboard = self.builder.build_dashboard(
            domain_metrics,
            window_id="2026-W17",
        )

        security = _domain_snapshot(dashboard, "security")
        self.assertEqual(security.status, "critical")
        self.assertEqual(dashboard.overall_status, "critical")

    def test_manifest_is_deterministic(self) -> None:
        dashboard = self.builder.build_dashboard(
            _baseline_metrics(),
            window_id="2026-W18",
        )

        first = dashboard.to_manifest()
        second = dashboard.to_manifest()
        self.assertEqual(first, second)

    def test_missing_required_domain_is_rejected(self) -> None:
        domain_metrics = _baseline_metrics()
        del domain_metrics["security"]

        with self.assertRaises(OperationsHealthDashboardError):
            self.builder.build_dashboard(domain_metrics, window_id="2026-W19")

    def test_duplicate_metric_ids_in_domain_are_rejected(self) -> None:
        domain_metrics = _baseline_metrics()
        domain_metrics["runtime"].append(
            OperationalHealthMetric(
                metric_id="uptime_ratio",
                value=0.99,
                target=0.995,
                direction="higher_is_better",
                warning_floor=0.9,
                critical_floor=0.75,
                weight=1.0,
                metadata={},
            )
        )

        with self.assertRaises(OperationsHealthDashboardError):
            self.builder.build_dashboard(domain_metrics, window_id="2026-W20")


def _domain_snapshot(dashboard, domain_id: str):
    for snapshot in dashboard.domain_snapshots:
        if snapshot.domain_id == domain_id:
            return snapshot
    raise AssertionError(f"Missing domain snapshot {domain_id}")


def _baseline_metrics() -> dict[str, list[OperationalHealthMetric]]:
    return {
        "runtime": [
            OperationalHealthMetric(
                metric_id="uptime_ratio",
                value=0.998,
                target=0.995,
                direction="higher_is_better",
                warning_floor=0.9,
                critical_floor=0.75,
                weight=1.2,
                metadata={"unit": "ratio"},
            ),
            OperationalHealthMetric(
                metric_id="p95_latency_ms",
                value=180.0,
                target=250.0,
                direction="lower_is_better",
                warning_floor=0.9,
                critical_floor=0.75,
                weight=1.0,
                metadata={"unit": "milliseconds"},
            ),
        ],
        "autonomy": [
            OperationalHealthMetric(
                metric_id="autonomous_success_ratio",
                value=0.992,
                target=0.99,
                direction="higher_is_better",
                warning_floor=0.9,
                critical_floor=0.75,
                weight=1.0,
                metadata={"unit": "ratio"},
            ),
            OperationalHealthMetric(
                metric_id="escalation_rate",
                value=0.02,
                target=0.03,
                direction="lower_is_better",
                warning_floor=0.9,
                critical_floor=0.75,
                weight=0.9,
                metadata={"unit": "ratio"},
            ),
        ],
        "security": [
            OperationalHealthMetric(
                metric_id="guardrail_enforcement_ratio",
                value=0.9995,
                target=0.999,
                direction="higher_is_better",
                warning_floor=0.9,
                critical_floor=0.75,
                weight=1.0,
                metadata={"unit": "ratio"},
            ),
            OperationalHealthMetric(
                metric_id="critical_incident_rate",
                value=0.001,
                target=0.002,
                direction="lower_is_better",
                warning_floor=0.9,
                critical_floor=0.75,
                weight=1.0,
                metadata={"unit": "ratio"},
            ),
        ],
    }


if __name__ == "__main__":
    unittest.main()