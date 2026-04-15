"""Tests for P11-T11 operator runbook and incident playbook finalization."""

from __future__ import annotations

import unittest

from runtime.orchestration import (
    OperatorRunbookError,
    OperatorRunbookFinalizer,
    build_default_operator_runbook_bundle,
    default_failure_injection_scenarios,
)
from runtime.security import IncidentPlaybookManager, IncidentPlaybookStep, build_default_incident_playbooks


class OperatorRunbookFinalizerTests(unittest.TestCase):
    def test_build_default_bundle_contains_required_playbooks_and_services(self) -> None:
        bundle = build_default_operator_runbook_bundle()

        self.assertTrue(
            {
                "incident.prompt_injection",
                "incident.secret_exposure",
                "incident.policy_anomaly",
            }.issubset(set(bundle.incident_playbook_ids))
        )
        self.assertEqual(
            set(bundle.critical_service_ids),
            {"orchestration", "memory", "configuration", "security", "release_pipeline"},
        )

    def test_bundle_markdown_contains_document_sections(self) -> None:
        bundle = build_default_operator_runbook_bundle()

        self.assertIn("# Operator Runbook Bundle", bundle.markdown)
        self.assertIn("## Operator Prompt Injection Containment", bundle.markdown)
        self.assertIn("## Operator Secret Exposure Recovery", bundle.markdown)
        self.assertIn("## Operator Policy Anomaly Response", bundle.markdown)

    def test_finalize_bundle_rejects_missing_required_playbooks(self) -> None:
        manager = IncidentPlaybookManager()
        manager.register_playbook(
            playbook_id="incident.prompt_injection",
            name="Prompt Injection",
            trigger_signals=("prompt_injection_attempt",),
            containment_steps=(
                IncidentPlaybookStep(
                    step_id="c1",
                    action="isolate",
                    parameters={},
                ),
            ),
            recovery_steps=(
                IncidentPlaybookStep(
                    step_id="r1",
                    action="recover",
                    parameters={},
                ),
            ),
        )

        with self.assertRaises(OperatorRunbookError):
            OperatorRunbookFinalizer().finalize_bundle(
                manager,
                failure_scenarios=default_failure_injection_scenarios(),
            )

    def test_bundle_manifest_is_deterministic(self) -> None:
        manager = build_default_incident_playbooks()
        bundle = OperatorRunbookFinalizer().finalize_bundle(
            manager,
            failure_scenarios=default_failure_injection_scenarios(),
        )

        first = bundle.to_manifest()
        second = bundle.to_manifest()
        self.assertEqual(first, second)

    def test_finalize_bundle_uses_custom_failure_scenario_coverage(self) -> None:
        manager = build_default_incident_playbooks()
        scenarios = list(default_failure_injection_scenarios())
        scenarios.append(
            scenarios[0].__class__(
                scenario_id="critical-observability-outage",
                title="Observability outage containment",
                service_id="observability",
                fault_type="dependency_outage",
                severity="high",
                target_response_seconds=35.0,
                expected_outcomes=("contained", "degraded"),
                metadata={"phase": "P11-T11"},
            )
        )

        bundle = OperatorRunbookFinalizer().finalize_bundle(
            manager,
            failure_scenarios=tuple(scenarios),
        )

        self.assertIn("observability", bundle.critical_service_ids)


if __name__ == "__main__":
    unittest.main()
