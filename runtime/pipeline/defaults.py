"""Default boundary implementations used for scaffolding and tests."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

from .models import ExecutionResult, PlanResult, PlannedTask, ReportResult, RunContext, ValidationResult


class DeterministicPlanner:
    def plan(self, context: RunContext) -> PlanResult:
        plan_id = sha256(context.goal.encode("utf-8")).hexdigest()[:12]
        task = PlannedTask(task_id=f"task-{plan_id}", description=context.goal)
        return PlanResult(plan_id=plan_id, tasks=[task], metadata={"strategy": "deterministic-default"})


class EchoExecutor:
    def execute(self, context: RunContext, plan: PlanResult) -> ExecutionResult:
        outputs = [{"task_id": task.task_id, "result": f"executed:{task.description}"} for task in plan.tasks]
        return ExecutionResult(status="success", outputs=outputs, metrics={"task_count": len(plan.tasks)})


class PassValidator:
    def validate(self, context: RunContext, plan: PlanResult, execution: ExecutionResult) -> ValidationResult:
        checks = [{"name": "execution_status", "passed": execution.status == "success"}]
        return ValidationResult(passed=all(check["passed"] for check in checks), checks=checks)


class SummaryReporter:
    def report(
        self,
        context: RunContext,
        plan: PlanResult,
        execution: ExecutionResult,
        validation: ValidationResult,
    ) -> ReportResult:
        summary = (
            f"run={context.run_id}; plan={plan.plan_id}; status={execution.status}; validation={validation.passed}"
        )
        return ReportResult(report_id=str(uuid4()), summary=summary, metadata={"pipeline": "default"})
