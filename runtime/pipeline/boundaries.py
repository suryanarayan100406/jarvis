"""Boundary protocols for planner, executor, validator, and reporter modules."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import ExecutionResult, PlanResult, ReportResult, RunContext, ValidationResult


@runtime_checkable
class PlannerBoundary(Protocol):
    def plan(self, context: RunContext) -> PlanResult:
        ...


@runtime_checkable
class ExecutorBoundary(Protocol):
    def execute(self, context: RunContext, plan: PlanResult) -> ExecutionResult:
        ...


@runtime_checkable
class ValidatorBoundary(Protocol):
    def validate(self, context: RunContext, plan: PlanResult, execution: ExecutionResult) -> ValidationResult:
        ...


@runtime_checkable
class ReporterBoundary(Protocol):
    def report(
        self,
        context: RunContext,
        plan: PlanResult,
        execution: ExecutionResult,
        validation: ValidationResult,
    ) -> ReportResult:
        ...
