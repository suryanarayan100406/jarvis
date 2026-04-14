"""Tests for P7-T12 operator drill scripts."""

from __future__ import annotations

import unittest

from runtime.security import (
    IncidentPlaybookStep,
    OperatorDrillScenario,
    OperatorEmergencyDrillRunner,
    create_drill_runner_with_default_playbooks,
    default_operator_drill_scenarios,
)
from runtime.security.incident_playbooks import IncidentPlaybookManager


class _DeterministicTimer:
    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self._index = 0

    def now(self) -> float:
        if self._index >= len(self._values):
            return self._values[-1]
        value = self._values[self._index]
        self._index += 1
        return value


class OperatorEmergencyDrillRunnerTests(unittest.TestCase):
    def _manager_with_handlers(self, status_mode: str = "recovered") -> IncidentPlaybookManager:
        def ok(step: IncidentPlaybookStep, _context: dict) -> dict:
            if status_mode == "degraded" and step.step_id == "recover-2":
                raise RuntimeError("optional telemetry restore failed")
            if status_mode == "failed" and step.step_id == "contain-1":
                raise RuntimeError("containment failed")
            return {"step": step.step_id, "ok": True}

        manager = IncidentPlaybookManager(handlers={
            "isolate_session": ok,
            "revoke_untrusted_tokens": ok,
            "enforce_safe_mode": ok,
            "reset_session_context": ok,
            "run_security_review": ok,
            "revoke_secret_access": ok,
            "block_replay_surface": ok,
            "rotate_exposed_secrets": ok,
            "verify_secret_integrity": ok,
            "freeze_high_risk_actions": ok,
            "open_escalation_ticket": ok,
            "rebaseline_policy": ok,
            "verify_operator_intent": ok,
        })

        from runtime.security import build_default_incident_playbooks

        build_default_incident_playbooks(manager)
        return manager

    def test_run_default_drills_passes_within_response_targets(self) -> None:
        manager = self._manager_with_handlers(status_mode="recovered")
        timer = _DeterministicTimer([0.0, 0.1, 1.0, 1.1, 2.0, 2.1])
        runner = OperatorEmergencyDrillRunner(manager, timer=timer.now)

        report = runner.run_drills(default_operator_drill_scenarios())

        self.assertEqual(report.total_drills, 3)
        self.assertEqual(report.passed, 3)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.degraded, 0)
        self.assertEqual(report.readiness_score, 1.0)

    def test_drill_degrades_when_response_budget_exceeded(self) -> None:
        manager = self._manager_with_handlers(status_mode="recovered")
        timer = _DeterministicTimer([0.0, 1.0])
        runner = OperatorEmergencyDrillRunner(manager, timer=timer.now)

        scenario = OperatorDrillScenario(
            drill_id="drill-slow",
            title="Slow response drill",
            incident_id="drill-inc-slow",
            playbook_id="incident.prompt_injection",
            target_response_seconds=0.2,
            expected_status=("recovered",),
            trigger_signals=("prompt_injection_attempt",),
            metadata={},
        )

        report = runner.run_drills([scenario])

        self.assertEqual(report.total_drills, 1)
        self.assertEqual(report.passed, 0)
        self.assertEqual(report.degraded, 1)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.drills[0].status, "degraded")

    def test_drill_fails_when_playbook_outcome_misses_expected_status(self) -> None:
        manager = self._manager_with_handlers(status_mode="failed")
        timer = _DeterministicTimer([0.0, 0.1])
        runner = OperatorEmergencyDrillRunner(manager, timer=timer.now)

        scenario = OperatorDrillScenario(
            drill_id="drill-fail",
            title="Failed containment drill",
            incident_id="drill-inc-fail",
            playbook_id="incident.prompt_injection",
            target_response_seconds=0.5,
            expected_status=("recovered",),
            trigger_signals=("prompt_injection_attempt",),
            metadata={},
        )

        report = runner.run_drills([scenario])

        self.assertEqual(report.failed, 1)
        self.assertEqual(report.readiness_score, 0.0)
        self.assertEqual(report.drills[0].status, "fail")

    def test_create_drill_runner_with_default_playbooks_uses_handlers(self) -> None:
        def ok(step: IncidentPlaybookStep, _context: dict) -> str:
            return f"ok:{step.step_id}"

        timer = _DeterministicTimer([0.0, 0.1, 1.0, 1.1, 2.0, 2.1])
        runner = create_drill_runner_with_default_playbooks(
            handlers={
                "isolate_session": ok,
                "revoke_untrusted_tokens": ok,
                "enforce_safe_mode": ok,
                "reset_session_context": ok,
                "run_security_review": ok,
                "revoke_secret_access": ok,
                "block_replay_surface": ok,
                "rotate_exposed_secrets": ok,
                "verify_secret_integrity": ok,
                "freeze_high_risk_actions": ok,
                "open_escalation_ticket": ok,
                "rebaseline_policy": ok,
                "verify_operator_intent": ok,
            },
            timer=timer.now,
        )

        report = runner.run_drills(default_operator_drill_scenarios())

        self.assertEqual(report.passed, 3)
        self.assertEqual(report.failed, 0)


if __name__ == "__main__":
    unittest.main()
