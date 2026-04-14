"""Tests for P2-T5 policy-aware validator stage."""

from __future__ import annotations

import unittest

from runtime.pipeline.models import ExecutionResult, PlanResult, PlannedTask, RunContext
from runtime.validator_stage import PolicyAwareValidatorStage


def _context() -> RunContext:
    return RunContext(run_id="run-val", goal="Validate outputs", actor_id="boss")


def _plan() -> PlanResult:
    return PlanResult(
        plan_id="plan-val",
        tasks=[
            PlannedTask(task_id="task-1", description="read status"),
            PlannedTask(task_id="task-2", description="update config"),
        ],
    )


class PolicyAwareValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = PolicyAwareValidatorStage()

    def test_success_with_allow_policy_decisions_passes(self) -> None:
        execution = ExecutionResult(
            status="success",
            outputs=[
                {"policy_decision": {"decision": "allow"}},
                {"policy_decision": {"decision": "allow"}},
            ],
        )

        result = self.validator.validate(_context(), _plan(), execution)

        self.assertTrue(result.passed)

    def test_require_approval_without_evidence_fails(self) -> None:
        execution = ExecutionResult(
            status="success",
            outputs=[
                {"policy_decision": {"decision": "allow"}},
                {"policy_decision": {"decision": "require_approval"}},
            ],
        )

        result = self.validator.validate(_context(), _plan(), execution)

        self.assertFalse(result.passed)
        self.assertIn("task-2:approval_missing", result.details["policy_violations"])

    def test_require_approval_with_evidence_passes(self) -> None:
        execution = ExecutionResult(
            status="success",
            outputs=[
                {"policy_decision": {"decision": "allow"}},
                {
                    "policy_decision": {"decision": "require_approval"},
                    "approval": {"approved": True},
                },
            ],
        )

        result = self.validator.validate(_context(), _plan(), execution)

        self.assertTrue(result.passed)

    def test_denied_policy_fails(self) -> None:
        execution = ExecutionResult(
            status="success",
            outputs=[
                {"policy_decision": {"decision": "allow"}},
                {"policy_decision": {"decision": "deny"}},
            ],
        )

        result = self.validator.validate(_context(), _plan(), execution)

        self.assertFalse(result.passed)
        self.assertIn("task-2:denied", result.details["policy_violations"])

    def test_failed_execution_status_fails_validation(self) -> None:
        execution = ExecutionResult(
            status="failed",
            outputs=[
                {"policy_decision": {"decision": "allow"}},
                {"policy_decision": {"decision": "allow"}},
            ],
        )

        result = self.validator.validate(_context(), _plan(), execution)

        self.assertFalse(result.passed)
        status_check = next(check for check in result.checks if check["name"] == "execution_status_success")
        self.assertFalse(status_check["passed"])


if __name__ == "__main__":
    unittest.main()
