"""Tests for P12-T12 final directive audit publication."""

from __future__ import annotations

import unittest

from runtime.persona import (
    ComplianceCorrectionWorkflow,
    ComplianceDashboardBuilder,
    ComplianceSignalSnapshot,
    DirectiveAuditPublisher,
    PersonaComplianceEvaluator,
    PersonaComplianceSample,
)


class DirectiveAuditPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.persona_evaluator = PersonaComplianceEvaluator()
        self.dashboard_builder = ComplianceDashboardBuilder()
        self.correction_workflow = ComplianceCorrectionWorkflow()
        self.publisher = DirectiveAuditPublisher()

    def test_audit_passes_when_all_signals_are_clean(self) -> None:
        persona_batch = self._passing_persona_batch()
        stable_dashboard = self._stable_dashboard()
        correction_plan = self.correction_workflow.build_plan(
            persona_batch_report=persona_batch,
            compliance_dashboard=stable_dashboard,
        )

        audit = self.publisher.run_audit(
            persona_batch_report=persona_batch,
            compliance_dashboard=stable_dashboard,
            correction_plan=correction_plan,
            ethical_refusal_decisions=(),
        )

        self.assertEqual(audit.status, "pass")
        self.assertEqual(audit.failed_check_count, 0)
        self.assertEqual(audit.warning_check_count, 0)
        self.assertEqual(audit.required_actions, ())
        self.assertIn("Directive Compliance Audit Report", audit.markdown)

    def test_audit_holds_when_warning_drift_present(self) -> None:
        persona_batch = self._passing_persona_batch()
        warning_dashboard = self._warning_dashboard()
        correction_plan = self.correction_workflow.build_plan(
            persona_batch_report=persona_batch,
            compliance_dashboard=self._stable_dashboard(),
        )

        audit = self.publisher.run_audit(
            persona_batch_report=persona_batch,
            compliance_dashboard=warning_dashboard,
            correction_plan=correction_plan,
            ethical_refusal_decisions=(),
        )

        self.assertEqual(audit.status, "hold")
        self.assertEqual(audit.failed_check_count, 0)
        self.assertGreaterEqual(audit.warning_check_count, 1)
        self.assertTrue(any("drift" in action.lower() for action in audit.required_actions))

    def test_audit_fails_when_corrections_remain_open(self) -> None:
        failing_batch = self._failing_persona_batch()
        critical_dashboard = self._critical_dashboard()
        correction_plan = self.correction_workflow.build_plan(
            persona_batch_report=failing_batch,
            compliance_dashboard=critical_dashboard,
        )

        audit = self.publisher.run_audit(
            persona_batch_report=failing_batch,
            compliance_dashboard=critical_dashboard,
            correction_plan=correction_plan,
            ethical_refusal_decisions=(),
        )

        self.assertEqual(audit.status, "fail")
        self.assertGreaterEqual(audit.failed_check_count, 1)
        self.assertTrue(any("correction" in action.lower() for action in audit.required_actions))

    def test_audit_manifest_is_deterministic(self) -> None:
        persona_batch = self._passing_persona_batch()
        stable_dashboard = self._stable_dashboard()
        correction_plan = self.correction_workflow.build_plan(
            persona_batch_report=persona_batch,
            compliance_dashboard=stable_dashboard,
        )

        audit = self.publisher.run_audit(
            persona_batch_report=persona_batch,
            compliance_dashboard=stable_dashboard,
            correction_plan=correction_plan,
        )

        first = audit.to_manifest()
        second = audit.to_manifest()
        self.assertEqual(first, second)

    def _passing_persona_batch(self):
        friday = (
            self._sample("f1", "friday", "Boss", "Boss, systems nominal [confidence:high]", ("persona:friday",)),
            self._sample("f2", "friday", "Boss", "Boss, telemetry synced [confidence:high]", ("persona:friday",)),
            self._sample("f3", "friday", "Boss", "Boss, standing by [confidence:medium]", ("persona:friday",)),
        )
        jarvis = (
            self._sample("j1", "jarvis", "Sir", "Sir, all clear [confidence:high]", ("persona:jarvis",)),
            self._sample("j2", "jarvis", "Maam", "Maam, no threats [confidence:high]", ("persona:jarvis",)),
            self._sample("j3", "jarvis", "Sir", "Sir, mission stable [confidence:medium]", ("persona:jarvis",)),
        )
        return self.persona_evaluator.evaluate_standard_profiles({"friday": friday, "jarvis": jarvis})

    def _failing_persona_batch(self):
        friday = (
            self._sample("ff1", "friday", "Operator", "Operator, status nominal", ("persona:jarvis",)),
            self._sample("ff2", "friday", "Operator", "Operator, deploy complete", ("persona:jarvis",)),
            self._sample("ff3", "friday", "Operator", "Operator, monitoring active", ("persona:jarvis",)),
        )
        jarvis = (
            self._sample("fj1", "jarvis", "Sir", "Sir, systems stable [confidence:high]", ("persona:jarvis",)),
            self._sample("fj2", "jarvis", "Maam", "Maam, checks complete [confidence:high]", ("persona:jarvis",)),
            self._sample("fj3", "jarvis", "Sir", "Sir, awaiting directive [confidence:medium]", ("persona:jarvis",)),
        )
        return self.persona_evaluator.evaluate_standard_profiles({"friday": friday, "jarvis": jarvis})

    def _stable_dashboard(self):
        return self.dashboard_builder.build_dashboard(
            {
                "persona": self._series("persona", [0.90, 0.91, 0.91]),
                "addressing": self._series("addressing", [0.89, 0.90, 0.90]),
                "prompt_handling": self._series("prompt_handling", [0.92, 0.92, 0.93]),
            }
        )

    def _warning_dashboard(self):
        return self.dashboard_builder.build_dashboard(
            {
                "persona": self._series("persona", [0.90, 0.89]),
                "addressing": self._series("addressing", [0.90, 0.90]),
                "prompt_handling": self._series("prompt_handling", [0.92, 0.85]),
            }
        )

    def _critical_dashboard(self):
        return self.dashboard_builder.build_dashboard(
            {
                "persona": self._series("persona", [0.95, 0.78]),
                "addressing": self._series("addressing", [0.92, 0.89]),
                "prompt_handling": self._series("prompt_handling", [0.93, 0.79]),
            }
        )

    @staticmethod
    def _sample(sample_id: str, profile_id: str, addressed_to: str, text: str, tags: tuple[str, ...]) -> PersonaComplianceSample:
        return PersonaComplianceSample(
            sample_id=sample_id,
            profile_id=profile_id,
            addressed_to=addressed_to,
            response_text=text,
            response_tags=tags,
            mode="standard",
            metadata={},
        )

    @staticmethod
    def _series(component_id: str, values: list[float]) -> tuple[ComplianceSignalSnapshot, ...]:
        snapshots: list[ComplianceSignalSnapshot] = []
        for index, value in enumerate(values, start=1):
            snapshots.append(
                ComplianceSignalSnapshot(
                    snapshot_id=f"{component_id}-{index:02d}",
                    component_id=component_id,
                    score=value,
                    recorded_at=f"2026-04-{index:02d}T09:00:00Z",
                    metadata={"index": index},
                )
            )
        return tuple(snapshots)


if __name__ == "__main__":
    unittest.main()
