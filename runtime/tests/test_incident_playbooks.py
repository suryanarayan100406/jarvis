"""Tests for P7-T8 incident playbooks for containment and recovery."""

from __future__ import annotations

import unittest

from runtime.security import (
    IncidentPlaybookManager,
    IncidentPlaybookStep,
    build_default_incident_playbooks,
)


class IncidentPlaybookManagerTests(unittest.TestCase):
    def test_execute_playbook_runs_containment_and_recovery_successfully(self) -> None:
        call_order: list[str] = []

        def isolate(step: IncidentPlaybookStep, context: dict) -> str:
            call_order.append(step.step_id)
            self.assertEqual(context["incident_id"], "inc-1")
            return "isolated"

        def safe_mode(step: IncidentPlaybookStep, _context: dict) -> dict:
            call_order.append(step.step_id)
            return {"mode": step.parameters.get("mode", "restricted")}

        def recover(step: IncidentPlaybookStep, _context: dict) -> str:
            call_order.append(step.step_id)
            return "recovered"

        manager = IncidentPlaybookManager(
            handlers={
                "isolate": isolate,
                "safe_mode": safe_mode,
                "recover": recover,
            }
        )
        manager.register_playbook(
            playbook_id="incident.custom",
            name="Custom incident flow",
            trigger_signals=("prompt_injection_attempt",),
            containment_steps=(
                IncidentPlaybookStep(step_id="c1", action="isolate", parameters={}),
                IncidentPlaybookStep(step_id="c2", action="safe_mode", parameters={"mode": "restricted"}),
            ),
            recovery_steps=(
                IncidentPlaybookStep(step_id="r1", action="recover", parameters={}),
            ),
        )

        result = manager.execute_playbook("incident.custom", incident_id="inc-1")

        self.assertEqual(result.status, "recovered")
        self.assertEqual(result.metrics["steps_total"], 3)
        self.assertEqual(result.metrics["steps_failed"], 0)
        self.assertEqual(call_order, ["c1", "c2", "r1"])

    def test_required_containment_failure_aborts_playbook(self) -> None:
        call_order: list[str] = []

        def fail_containment(step: IncidentPlaybookStep, _context: dict) -> str:
            call_order.append(step.step_id)
            raise RuntimeError("containment failed")

        def should_not_run(step: IncidentPlaybookStep, _context: dict) -> str:
            call_order.append(step.step_id)
            return "unexpected"

        manager = IncidentPlaybookManager(
            handlers={
                "fail": fail_containment,
                "recover": should_not_run,
            }
        )
        manager.register_playbook(
            playbook_id="incident.fail",
            name="Fail containment",
            containment_steps=(IncidentPlaybookStep(step_id="c1", action="fail", parameters={}),),
            recovery_steps=(IncidentPlaybookStep(step_id="r1", action="recover", parameters={}),),
        )

        result = manager.execute_playbook("incident.fail", incident_id="inc-2")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.metrics["steps_total"], 1)
        self.assertEqual(result.metrics["required_failures"], 1)
        self.assertEqual(call_order, ["c1"])

    def test_optional_recovery_failure_results_in_degraded_status(self) -> None:
        def contain(_step: IncidentPlaybookStep, _context: dict) -> str:
            return "contained"

        def optional_recovery_fail(_step: IncidentPlaybookStep, _context: dict) -> str:
            raise RuntimeError("telemetry restore delayed")

        def required_recovery_ok(_step: IncidentPlaybookStep, _context: dict) -> str:
            return "primary recovery done"

        manager = IncidentPlaybookManager(
            handlers={
                "contain": contain,
                "recover_optional": optional_recovery_fail,
                "recover_required": required_recovery_ok,
            }
        )
        manager.register_playbook(
            playbook_id="incident.degraded",
            name="Degraded recovery",
            containment_steps=(IncidentPlaybookStep(step_id="c1", action="contain", parameters={}),),
            recovery_steps=(
                IncidentPlaybookStep(step_id="r1", action="recover_optional", parameters={}, required=False),
                IncidentPlaybookStep(step_id="r2", action="recover_required", parameters={}),
            ),
        )

        result = manager.execute_playbook("incident.degraded", incident_id="inc-3")

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.metrics["optional_failures"], 1)
        self.assertEqual(result.metrics["required_failures"], 0)

    def test_stop_after_containment_returns_contained_status(self) -> None:
        call_order: list[str] = []

        def contain(step: IncidentPlaybookStep, _context: dict) -> str:
            call_order.append(step.step_id)
            return "ok"

        def recover(step: IncidentPlaybookStep, _context: dict) -> str:
            call_order.append(step.step_id)
            return "ok"

        manager = IncidentPlaybookManager(handlers={"contain": contain, "recover": recover})
        manager.register_playbook(
            playbook_id="incident.contained",
            name="Contain only",
            containment_steps=(IncidentPlaybookStep(step_id="c1", action="contain", parameters={}),),
            recovery_steps=(IncidentPlaybookStep(step_id="r1", action="recover", parameters={}),),
        )

        result = manager.execute_playbook(
            "incident.contained",
            incident_id="inc-4",
            stop_after_containment=True,
        )

        self.assertEqual(result.status, "contained")
        self.assertEqual(result.metrics["containment_steps_executed"], 1)
        self.assertEqual(result.metrics["recovery_steps_executed"], 0)
        self.assertEqual(call_order, ["c1"])

    def test_recommend_playbooks_filters_by_trigger_signal(self) -> None:
        manager = IncidentPlaybookManager()
        manager.register_playbook(
            playbook_id="incident.a",
            name="A",
            trigger_signals=("signal.alpha",),
            containment_steps=(IncidentPlaybookStep(step_id="c1", action="a", parameters={}),),
            recovery_steps=(IncidentPlaybookStep(step_id="r1", action="a", parameters={}),),
        )
        manager.register_playbook(
            playbook_id="incident.b",
            name="B",
            trigger_signals=("signal.beta", "signal.alpha"),
            containment_steps=(
                IncidentPlaybookStep(step_id="c1", action="b", parameters={}),
                IncidentPlaybookStep(step_id="c2", action="b", parameters={}),
            ),
            recovery_steps=(IncidentPlaybookStep(step_id="r1", action="b", parameters={}),),
        )

        matches = manager.recommend_playbooks("signal.alpha")

        self.assertEqual([item.playbook_id for item in matches], ["incident.b", "incident.a"])

    def test_build_default_incident_playbooks_registers_security_playbooks(self) -> None:
        manager = build_default_incident_playbooks()
        ids = {playbook.playbook_id for playbook in manager.list_playbooks()}

        self.assertIn("incident.prompt_injection", ids)
        self.assertIn("incident.secret_exposure", ids)
        self.assertIn("incident.policy_anomaly", ids)


if __name__ == "__main__":
    unittest.main()
