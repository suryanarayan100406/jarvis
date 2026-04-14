"""Tests for P7-T1 threat model prioritization and mitigation mapping."""

from __future__ import annotations

import unittest

from runtime.security import ThreatModelError, ThreatModelRegistry, build_default_threat_model


class ThreatModelRegistryTests(unittest.TestCase):
    def test_prioritized_cases_sorted_by_risk_score(self) -> None:
        model = ThreatModelRegistry()
        model.register_mitigation(
            mitigation_id="m1",
            name="Control 1",
            description="desc",
            owner="security",
        )

        model.add_abuse_case(
            case_id="c-low",
            title="Low",
            description="low risk",
            attack_surface="chat",
            likelihood=2,
            impact=2,
            mitigation_ids=("m1",),
        )
        model.add_abuse_case(
            case_id="c-high",
            title="High",
            description="high risk",
            attack_surface="chat",
            likelihood=5,
            impact=4,
            mitigation_ids=("m1",),
        )
        model.add_abuse_case(
            case_id="c-medium",
            title="Medium",
            description="medium risk",
            attack_surface="chat",
            likelihood=3,
            impact=3,
            mitigation_ids=("m1",),
        )

        ordered = model.list_abuse_cases(prioritized=True)

        self.assertEqual([case.case_id for case in ordered], ["c-high", "c-medium", "c-low"])

    def test_finalize_report_flags_uncovered_cases(self) -> None:
        model = ThreatModelRegistry()
        model.register_mitigation(
            mitigation_id="m1",
            name="Control 1",
            description="desc",
            owner="security",
        )
        model.add_abuse_case(
            case_id="c-covered",
            title="Covered",
            description="covered",
            attack_surface="chat",
            likelihood=4,
            impact=4,
            mitigation_ids=("m1",),
        )
        model.add_abuse_case(
            case_id="c-uncovered",
            title="Uncovered",
            description="missing mitigation",
            attack_surface="chat",
            likelihood=4,
            impact=5,
            mitigation_ids=("m-missing",),
        )

        report = model.finalize_report()

        self.assertEqual(report.total_cases, 2)
        self.assertEqual(report.uncovered_case_ids, ("c-uncovered",))
        self.assertEqual(report.mitigation_coverage_ratio, 0.5)

    def test_map_case_mitigations_returns_registered_controls_only(self) -> None:
        model = ThreatModelRegistry()
        model.register_mitigation(
            mitigation_id="m1",
            name="Control 1",
            description="desc",
            owner="security",
        )
        model.register_mitigation(
            mitigation_id="m2",
            name="Control 2",
            description="desc",
            owner="security",
        )
        model.add_abuse_case(
            case_id="c-map",
            title="Map",
            description="map mitigations",
            attack_surface="chat",
            likelihood=3,
            impact=4,
            mitigation_ids=("m2", "m1", "missing"),
        )

        mapped = model.map_case_mitigations("c-map")

        self.assertEqual([item.mitigation_id for item in mapped], ["m1", "m2"])

    def test_invalid_scores_and_duplicates_raise(self) -> None:
        model = ThreatModelRegistry()
        model.register_mitigation(
            mitigation_id="m1",
            name="Control 1",
            description="desc",
            owner="security",
        )

        with self.assertRaises(ThreatModelError):
            model.add_abuse_case(
                case_id="c-bad",
                title="Bad",
                description="bad score",
                attack_surface="chat",
                likelihood=0,
                impact=3,
            )

        model.add_abuse_case(
            case_id="c-ok",
            title="OK",
            description="good",
            attack_surface="chat",
            likelihood=3,
            impact=3,
        )

        with self.assertRaises(ThreatModelError):
            model.add_abuse_case(
                case_id="c-ok",
                title="Dup",
                description="dup",
                attack_surface="chat",
                likelihood=3,
                impact=3,
            )

    def test_default_threat_model_has_coverage_for_priority_cases(self) -> None:
        model = build_default_threat_model()
        report = model.finalize_report()

        self.assertGreaterEqual(report.total_cases, 4)
        self.assertGreaterEqual(report.total_mitigations, 5)
        self.assertGreaterEqual(report.mitigation_coverage_ratio, 1.0)
        self.assertEqual(len(report.uncovered_case_ids), 0)
        self.assertIn("abuse.prompt_injection", report.prioritized_case_ids)


if __name__ == "__main__":
    unittest.main()
