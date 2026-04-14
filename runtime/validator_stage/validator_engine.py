"""Policy-aware validator stage for run execution outputs."""

from __future__ import annotations

from typing import Any

from runtime.pipeline.models import ExecutionResult, PlanResult, RunContext, ValidationResult
from runtime.policy import PolicyEngine


class PolicyAwareValidatorStage:
    """Validates execution outcomes with policy-aware checks and approval evidence."""

    def __init__(self, policy_engine: PolicyEngine | None = None) -> None:
        self.policy_engine = policy_engine or PolicyEngine()

    def validate(self, context: RunContext, plan: PlanResult, execution: ExecutionResult) -> ValidationResult:
        checks: list[dict[str, Any]] = []

        status_ok = execution.status == "success"
        checks.append(
            {
                "name": "execution_status_success",
                "passed": status_ok,
                "details": f"status={execution.status}",
            }
        )

        count_ok = len(execution.outputs) == len(plan.tasks)
        checks.append(
            {
                "name": "output_count_matches_plan",
                "passed": count_ok,
                "details": f"outputs={len(execution.outputs)} tasks={len(plan.tasks)}",
            }
        )

        policy_violations: list[str] = []
        for index, task in enumerate(plan.tasks):
            output = execution.outputs[index] if index < len(execution.outputs) else {}
            decision = self._resolve_policy_decision(context, task.description, output)

            if decision == "deny":
                policy_violations.append(f"{task.task_id}:denied")
                continue

            if decision == "require_approval":
                approved = bool(output.get("approval", {}).get("approved", False))
                if not approved:
                    policy_violations.append(f"{task.task_id}:approval_missing")

        checks.append(
            {
                "name": "policy_compliance",
                "passed": len(policy_violations) == 0,
                "details": "none" if not policy_violations else ",".join(policy_violations),
            }
        )

        passed = all(check["passed"] for check in checks)
        return ValidationResult(
            passed=passed,
            checks=checks,
            details={"policy_violations": policy_violations},
        )

    def _resolve_policy_decision(self, context: RunContext, task_description: str, output: dict[str, Any]) -> str:
        decision_payload = output.get("policy_decision") if isinstance(output, dict) else None
        if isinstance(decision_payload, dict) and isinstance(decision_payload.get("decision"), str):
            return decision_payload["decision"]

        inferred = self.policy_engine.evaluate(
            {
                "actor": {"role": "primary_user"},
                "tool": {"name": "executor", "action": task_description.lower().replace(" ", "_")},
                "target": {"scope": "local", "environment": "dev"},
                "execution": {"dry_run": False},
            }
        )
        return inferred.decision
