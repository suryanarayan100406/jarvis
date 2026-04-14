"""Registry for runtime module boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from .boundaries import ExecutorBoundary, PlannerBoundary, ReporterBoundary, ValidatorBoundary
from .models import ReportResult, RunContext


class ModuleBoundaryError(ValueError):
    """Raised when required runtime module boundaries are missing or invalid."""


@dataclass
class RuntimeModuleRegistry:
    planner: PlannerBoundary | None = None
    executor: ExecutorBoundary | None = None
    validator: ValidatorBoundary | None = None
    reporter: ReporterBoundary | None = None

    def set_planner(self, planner: PlannerBoundary) -> None:
        self.planner = planner

    def set_executor(self, executor: ExecutorBoundary) -> None:
        self.executor = executor

    def set_validator(self, validator: ValidatorBoundary) -> None:
        self.validator = validator

    def set_reporter(self, reporter: ReporterBoundary) -> None:
        self.reporter = reporter

    def validate_boundaries(self) -> None:
        missing = []
        if self.planner is None:
            missing.append("planner")
        if self.executor is None:
            missing.append("executor")
        if self.validator is None:
            missing.append("validator")
        if self.reporter is None:
            missing.append("reporter")

        if missing:
            raise ModuleBoundaryError(f"Missing module boundaries: {', '.join(missing)}")

    def run_pipeline(self, context: RunContext) -> ReportResult:
        self.validate_boundaries()
        assert self.planner is not None
        assert self.executor is not None
        assert self.validator is not None
        assert self.reporter is not None

        plan = self.planner.plan(context)
        execution = self.executor.execute(context, plan)
        validation = self.validator.validate(context, plan, execution)
        return self.reporter.report(context, plan, execution, validation)
