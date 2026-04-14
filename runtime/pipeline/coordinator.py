"""Deterministic run coordinator for orchestration pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.control import KillSwitchController
from runtime.orchestration import InvalidRunTransitionError, RunStateMachine, TransitionRecord

from .models import ExecutionResult, PlanResult, ReportResult, RunContext, ValidationResult
from .registry import RuntimeModuleRegistry


@dataclass(frozen=True)
class CoordinatedRunResult:
    run_id: str
    plan: PlanResult
    execution: ExecutionResult
    validation: ValidationResult
    report: ReportResult
    transitions: list[TransitionRecord]


class RunCoordinatorError(RuntimeError):
    """Raised when coordinated run execution fails."""

    def __init__(self, message: str, stage: str, transitions: list[TransitionRecord]) -> None:
        self.stage = stage
        self.transitions = transitions
        super().__init__(message)


class RunCoordinator:
    """Coordinates deterministic stage execution across registered module boundaries."""

    def __init__(self, registry: RuntimeModuleRegistry, kill_switch: KillSwitchController | None = None) -> None:
        self.registry = registry
        self.kill_switch = kill_switch

    def run(self, context: RunContext) -> CoordinatedRunResult:
        self.registry.validate_boundaries()

        machine = RunStateMachine(run_id=context.run_id)

        try:
            self._guard_execution()
            assert self.registry.planner is not None
            plan = self.registry.planner.plan(context)
            machine.transition_to("execute", reason="plan_completed")

            self._guard_execution()
            assert self.registry.executor is not None
            execution = self.registry.executor.execute(context, plan)
            machine.transition_to("validate", reason="execution_completed")

            self._guard_execution()
            assert self.registry.validator is not None
            validation = self.registry.validator.validate(context, plan, execution)
            machine.transition_to("report", reason="validation_completed")

            self._guard_execution()
            assert self.registry.reporter is not None
            report = self.registry.reporter.report(context, plan, execution, validation)
            machine.transition_to("completed", reason="report_completed")

            return CoordinatedRunResult(
                run_id=context.run_id,
                plan=plan,
                execution=execution,
                validation=validation,
                report=report,
                transitions=machine.history,
            )
        except Exception as exc:
            try:
                if machine.can_transition("failed"):
                    machine.transition_to("failed", reason=f"coordinator_error:{type(exc).__name__}")
                stage = machine.current_stage
            except InvalidRunTransitionError:
                stage = machine.current_stage

            raise RunCoordinatorError(
                message=f"Run coordination failed at stage {stage}: {exc}",
                stage=stage,
                transitions=machine.history,
            ) from exc

    def _guard_execution(self) -> None:
        if self.kill_switch is not None:
            self.kill_switch.assert_can_execute()
