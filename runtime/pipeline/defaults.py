"""Default boundary implementations used for scaffolding and tests."""

from __future__ import annotations

from uuid import uuid4

from runtime.planner import PlannerInterfaceAdapter

from .models import ExecutionResult, PlanResult, ReportResult, RunContext, ValidationResult


class DeterministicPlanner:
    def __init__(self) -> None:
        self._adapter = PlannerInterfaceAdapter()

    def plan(self, context: RunContext) -> PlanResult:
        plan_result = self._adapter.plan(context)
        metadata = dict(plan_result.metadata)
        metadata["strategy"] = "deterministic-default"
        return PlanResult(plan_id=plan_result.plan_id, tasks=plan_result.tasks, metadata=metadata)


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
