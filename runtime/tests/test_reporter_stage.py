"""Tests for P2-T6 reporter stage summary and artifact references."""

from __future__ import annotations

import unittest

from runtime.pipeline.models import ExecutionResult, PlanResult, PlannedTask, RunContext, ValidationResult
from runtime.reporter import ArtifactReporterStage


class ArtifactReporterStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reporter = ArtifactReporterStage()
        self.context = RunContext(run_id="run-report", goal="Create report", actor_id="boss")
        self.plan = PlanResult(
            plan_id="plan-report",
            tasks=[
                PlannedTask(task_id="task-1", description="step1"),
                PlannedTask(task_id="task-2", description="step2"),
            ],
        )

    def test_summary_contains_core_fields(self) -> None:
        execution = ExecutionResult(status="success", outputs=[])
        validation = ValidationResult(passed=True, checks=[])

        report = self.reporter.report(self.context, self.plan, execution, validation)

        self.assertIn("run=run-report", report.summary)
        self.assertIn("plan=plan-report", report.summary)
        self.assertIn("execution=success", report.summary)
        self.assertIn("validation=True", report.summary)

    def test_artifact_references_are_collected_and_deduplicated(self) -> None:
        shared = {"artifact_id": "a-1", "path": "logs/run.log"}
        execution = ExecutionResult(
            status="success",
            outputs=[{"artifact": shared}, {"artifact": shared}],
            artifacts=[shared],
        )
        validation = ValidationResult(passed=True, checks=[])

        report = self.reporter.report(self.context, self.plan, execution, validation)

        self.assertEqual(len(report.artifacts), 1)

    def test_metadata_contains_validation_and_counts(self) -> None:
        execution = ExecutionResult(status="failed", outputs=[{}])
        validation = ValidationResult(passed=False, checks=[{"name": "status", "passed": False}])

        report = self.reporter.report(self.context, self.plan, execution, validation)

        self.assertFalse(report.metadata["validation_passed"])
        self.assertEqual(report.metadata["task_count"], 2)
        self.assertEqual(report.metadata["execution_status"], "failed")


if __name__ == "__main__":
    unittest.main()
