"""Baseline integration tests for Phase 1 foundation components (P1-T7)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from runtime.audit import ImmutableAuditWriter
from runtime.control import KillSwitchActivatedError, KillSwitchController
from runtime.orchestration import RunStateMachine
from runtime.policy import PolicyEngine


class FoundationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.temp_dir.name) / "foundation-audit.log"
        self.audit_writer = ImmutableAuditWriter(self.audit_path)
        self.policy_engine = PolicyEngine()
        self.kill_switch = KillSwitchController()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_end_to_end_run_with_full_audit_trail(self) -> None:
        run_id = str(uuid4())
        machine = RunStateMachine(run_id=run_id)
        request = {
            "actor": {"role": "primary_user"},
            "tool": {"name": "terminal", "action": "get_status"},
            "target": {"scope": "local", "environment": "dev"},
            "execution": {"dry_run": False},
            "policy_context": {"risk_tier": "low"},
        }

        decision = self.policy_engine.evaluate(request)
        self.assertEqual(decision.decision, "allow")

        self.audit_writer.append_event(
            {
                "event_type": "runtime.plan.completed",
                "payload": {"run_id": run_id, "stage": "plan"},
                "policy_decision": decision.as_dict(),
            }
        )
        machine.transition_to("execute", reason="plan_approved")

        self.audit_writer.append_event(
            {
                "event_type": "runtime.execute.completed",
                "payload": {"run_id": run_id, "stage": "execute"},
                "policy_decision": decision.as_dict(),
            }
        )
        machine.transition_to("validate", reason="execution_complete")

        self.audit_writer.append_event(
            {
                "event_type": "runtime.validate.completed",
                "payload": {"run_id": run_id, "stage": "validate"},
                "policy_decision": decision.as_dict(),
            }
        )
        machine.transition_to("report", reason="validation_passed")
        machine.transition_to("completed", reason="report_published")

        self.audit_writer.append_event(
            {
                "event_type": "runtime.report.completed",
                "payload": {"run_id": run_id, "stage": "completed"},
                "policy_decision": decision.as_dict(),
            }
        )

        valid, issues = self.audit_writer.verify_chain()
        self.assertTrue(valid)
        self.assertEqual(issues, [])
        self.assertEqual(machine.current_stage, "completed")
        self.assertEqual(len(self.audit_path.read_text(encoding="utf-8").splitlines()), 4)

    def test_mid_run_kill_switch_interruption(self) -> None:
        run_id = str(uuid4())
        machine = RunStateMachine(run_id=run_id)
        machine.transition_to("execute", reason="plan_ready")

        event = self.kill_switch.activate(reason="operator_emergency_stop", actor="boss")
        self.assertTrue(self.kill_switch.is_active())
        self.assertEqual(event.state, "active")

        with self.assertRaises(KillSwitchActivatedError):
            self.kill_switch.run_guarded(lambda: "should_not_run")

        machine.transition_to("cancelled", reason="kill_switch_active")
        self.audit_writer.append_event(
            {
                "event_type": "runtime.kill_switch.activated",
                "severity": "critical",
                "payload": {
                    "run_id": run_id,
                    "reason": "operator_emergency_stop",
                    "stage": machine.current_stage,
                },
            }
        )

        valid, issues = self.audit_writer.verify_chain()
        self.assertTrue(valid)
        self.assertEqual(issues, [])
        self.assertEqual(machine.current_stage, "cancelled")

    def test_policy_denial_propagates_to_failed_execution(self) -> None:
        run_id = str(uuid4())
        machine = RunStateMachine(run_id=run_id)
        denied_request = {
            "actor": {"role": "limited_user"},
            "tool": {"name": "door_lock", "action": "unlock_main_door"},
            "target": {"scope": "physical", "environment": "prod"},
            "execution": {"dry_run": False},
            "policy_context": {"risk_tier": "critical"},
        }

        decision = self.policy_engine.evaluate(denied_request)
        self.assertEqual(decision.decision, "deny")

        machine.transition_to("failed", reason="policy_denied")
        self.audit_writer.append_event(
            {
                "event_type": "runtime.execution.denied",
                "severity": "warn",
                "payload": {"run_id": run_id, "reason": "policy_denied"},
                "policy_decision": decision.as_dict(),
            }
        )

        valid, issues = self.audit_writer.verify_chain()
        self.assertTrue(valid)
        self.assertEqual(issues, [])
        self.assertEqual(machine.current_stage, "failed")

        recorded = json.loads(self.audit_path.read_text(encoding="utf-8").strip())
        self.assertEqual(recorded["policy_decision"]["decision"], "deny")


if __name__ == "__main__":
    unittest.main()
