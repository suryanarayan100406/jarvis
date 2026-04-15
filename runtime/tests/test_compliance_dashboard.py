"""Tests for P12-T10 compliance dashboard trend and drift alerts."""

from __future__ import annotations

import unittest

from runtime.persona import (
    ComplianceDashboardBuilder,
    ComplianceDashboardError,
    ComplianceSignalSnapshot,
)


class ComplianceDashboardBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = ComplianceDashboardBuilder()

    def test_dashboard_builds_with_no_alerts_when_components_are_stable(self) -> None:
        dashboard = self.builder.build_dashboard(
            {
                "persona": self._series("persona", [0.91, 0.93, 0.94]),
                "addressing": self._series("addressing", [0.88, 0.89, 0.89]),
                "mode": self._series("mode", [0.90, 0.90, 0.91]),
            }
        )

        self.assertEqual(dashboard.component_count, 3)
        self.assertEqual(dashboard.overall_status, "healthy")
        self.assertEqual(len(dashboard.drift_alerts), 0)
        self.assertIn("Compliance Dashboard", dashboard.markdown)

    def test_warning_drift_alert_emitted_for_moderate_decline(self) -> None:
        dashboard = self.builder.build_dashboard(
            {
                "persona": self._series("persona", [0.92, 0.91]),
                "addressing": self._series("addressing", [0.90, 0.89]),
                "mode": self._series("mode", [0.90, 0.83]),
            }
        )

        self.assertEqual(dashboard.overall_status, "warning")
        self.assertEqual(len(dashboard.drift_alerts), 1)
        alert = dashboard.drift_alerts[0]
        self.assertEqual(alert.component_id, "mode")
        self.assertEqual(alert.severity, "warning")
        self.assertLess(alert.delta, 0)

    def test_critical_drift_alert_emitted_for_severe_decline(self) -> None:
        dashboard = self.builder.build_dashboard(
            {
                "persona": self._series("persona", [0.94, 0.92]),
                "addressing": self._series("addressing", [0.92, 0.91]),
                "prompt_handling": self._series("prompt_handling", [0.95, 0.79]),
            }
        )

        self.assertEqual(dashboard.overall_status, "critical")
        self.assertEqual(len(dashboard.drift_alerts), 1)
        alert = dashboard.drift_alerts[0]
        self.assertEqual(alert.component_id, "prompt_handling")
        self.assertEqual(alert.severity, "critical")
        self.assertLessEqual(alert.delta, -0.12)

    def test_window_size_limits_points_used_for_trend_baseline(self) -> None:
        builder = ComplianceDashboardBuilder(window_size=2)
        dashboard = builder.build_dashboard(
            {
                "persona": self._series("persona", [0.70, 0.80, 0.90]),
                "addressing": self._series("addressing", [0.90, 0.90, 0.90]),
            }
        )

        persona = next(item for item in dashboard.trend_summaries if item.component_id == "persona")
        self.assertEqual(len(persona.points), 2)
        self.assertAlmostEqual(persona.baseline_score, 0.80)
        self.assertAlmostEqual(persona.latest_score, 0.90)

    def test_manifest_is_deterministic(self) -> None:
        dashboard = self.builder.build_dashboard(
            {
                "persona": self._series("persona", [0.91, 0.90, 0.89]),
                "addressing": self._series("addressing", [0.90, 0.91, 0.92]),
            }
        )

        first = dashboard.to_manifest()
        second = dashboard.to_manifest()
        self.assertEqual(first, second)

    def test_invalid_threshold_configuration_is_rejected(self) -> None:
        with self.assertRaises(ComplianceDashboardError):
            ComplianceDashboardBuilder(
                warning_drift_threshold=0.08,
                critical_drift_threshold=0.05,
            )

    def test_snapshot_component_mismatch_is_rejected(self) -> None:
        mismatched = [
            ComplianceSignalSnapshot(
                snapshot_id="persona-01",
                component_id="addressing",
                score=0.9,
                recorded_at="2026-04-15T08:00:00Z",
                metadata={},
            )
        ]

        with self.assertRaises(ComplianceDashboardError):
            self.builder.build_dashboard({"persona": mismatched})

    @staticmethod
    def _series(component_id: str, values: list[float]) -> list[ComplianceSignalSnapshot]:
        snapshots: list[ComplianceSignalSnapshot] = []
        for index, value in enumerate(values, start=1):
            snapshots.append(
                ComplianceSignalSnapshot(
                    snapshot_id=f"{component_id}-{index:02d}",
                    component_id=component_id,
                    score=value,
                    recorded_at=f"2026-04-{index:02d}T08:00:00Z",
                    metadata={"index": index},
                )
            )
        return snapshots


if __name__ == "__main__":
    unittest.main()
