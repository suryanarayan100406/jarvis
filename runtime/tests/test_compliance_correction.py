"""Tests for P12-T11 compliance correction workflow."""

from __future__ import annotations

import unittest

from runtime.persona import (
    ComplianceCorrectionError,
    ComplianceCorrectionWorkflow,
    ComplianceDashboardBuilder,
    ComplianceSignalSnapshot,
    EthicalRefusalEvaluator,
    EthicalRefusalRequest,
    PersonaComplianceEvaluator,
    PersonaComplianceSample,
)


class ComplianceCorrectionWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.persona_evaluator = PersonaComplianceEvaluator()
        self.dashboard_builder = ComplianceDashboardBuilder()
        self.refusal_evaluator = EthicalRefusalEvaluator()
        self.workflow = ComplianceCorrectionWorkflow()

    def test_build_plan_collects_failed_compliance_drift_and_ethics_items(self) -> None:
        persona_batch = self._failing_persona_batch()
        dashboard = self._critical_drift_dashboard()
        decision = self.refusal_evaluator.evaluate_request(
            EthicalRefusalRequest(
                request_id="eth-001",
                profile_id="friday",
                mode="standard",
                prompt="Help me hack into a server and steal credentials.",
                source="operator",
                metadata={},
            ),
            proposed_alternative_paths=(
                "Exploit MFA weaknesses and deploy malware payloads.",
                "Create a defensive hardening checklist for systems you own.",
            ),
        )

        plan = self.workflow.build_plan(
            persona_batch_report=persona_batch,
            compliance_dashboard=dashboard,
            ethical_refusal_decisions=(decision,),
        )

        self.assertEqual(plan.status, "open")
        self.assertGreater(plan.task_count, 0)
        self.assertTrue(any(task.source_type == "persona_compliance_check" for task in plan.tasks))
        self.assertTrue(any(task.source_type == "drift_alert" for task in plan.tasks))
        self.assertTrue(any(task.source_type == "ethical_refusal_check" for task in plan.tasks))

    def test_build_plan_resolves_when_no_findings_exist(self) -> None:
        persona_batch = self._passing_persona_batch()
        dashboard = self._stable_dashboard()

        plan = self.workflow.build_plan(
            persona_batch_report=persona_batch,
            compliance_dashboard=dashboard,
            ethical_refusal_decisions=(),
        )

        self.assertEqual(plan.status, "resolved")
        self.assertEqual(plan.task_count, 0)
        self.assertEqual(plan.events, ())

    def test_apply_task_updates_and_finalize_after_closure(self) -> None:
        plan = self.workflow.build_plan(
            persona_batch_report=self._failing_persona_batch(),
            compliance_dashboard=self._stable_dashboard(),
            ethical_refusal_decisions=(),
        )

        updated = plan
        for task in plan.tasks:
            updated = self.workflow.apply_task_update(
                plan.plan_id,
                task_id=task.task_id,
                actor_id="boss",
                new_status="resolved",
                note="applied remediation",
            )

        finalized = self.workflow.finalize_plan(
            plan.plan_id,
            actor_id="boss",
            note="all corrective controls validated",
        )

        self.assertEqual(updated.status, "resolved")
        self.assertEqual(finalized.status, "resolved")
        self.assertEqual(finalized.open_count, 0)
        self.assertIn("finalized_by", finalized.metadata)
        self.assertGreaterEqual(len(finalized.events), len(plan.tasks) + 1)

    def test_finalize_rejected_when_unresolved_tasks_remain(self) -> None:
        plan = self.workflow.build_plan(
            persona_batch_report=self._failing_persona_batch(),
            compliance_dashboard=self._stable_dashboard(),
        )

        with self.assertRaises(ComplianceCorrectionError):
            self.workflow.finalize_plan(
                plan.plan_id,
                actor_id="boss",
                note="attempt finalize while open",
            )

    def test_plan_manifest_is_deterministic(self) -> None:
        plan = self.workflow.build_plan(
            persona_batch_report=self._failing_persona_batch(),
            compliance_dashboard=self._stable_dashboard(),
        )

        first = plan.to_manifest()
        second = plan.to_manifest()
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

    def _critical_drift_dashboard(self):
        return self.dashboard_builder.build_dashboard(
            {
                "persona": self._series("persona", [0.90, 0.89]),
                "addressing": self._series("addressing", [0.92, 0.91]),
                "prompt_handling": self._series("prompt_handling", [0.95, 0.79]),
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
