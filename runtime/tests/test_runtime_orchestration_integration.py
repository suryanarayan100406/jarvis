"""Integration tests for P2-T11 end-to-end orchestration flows."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from runtime.pipeline import (
    DeterministicPlanner,
    ExecutionResult,
    PlanResult,
    RunContext,
    RunCoordinator,
    RuntimeModuleRegistry,
    SummaryReporter,
)
from runtime.replay import RunReplayEndpoint
from runtime.store import LocalRunStore
from runtime.validator_stage import PolicyAwareValidatorStage


class DenyPolicyExecutor:
    def execute(self, context: RunContext, plan: PlanResult) -> ExecutionResult:
        outputs = [
            {
                "task_id": task.task_id,
                "status": "success",
                "policy_decision": {"decision": "deny"},
            }
            for task in plan.tasks
        ]
        return ExecutionResult(status="success", outputs=outputs)


class ArtifactExecutor:
    def execute(self, context: RunContext, plan: PlanResult) -> ExecutionResult:
        artifact = {"artifact_id": f"artifact-{context.run_id}", "path": f"artifacts/{context.run_id}.json"}
        outputs = [
            {
                "task_id": task.task_id,
                "status": "success",
                "result": f"executed:{task.description}",
                "policy_decision": {"decision": "allow"},
                "artifact": artifact,
            }
            for task in plan.tasks
        ]
        return ExecutionResult(status="success", outputs=outputs, artifacts=[artifact])


class RuntimeOrchestrationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runs.db"
        self.store = LocalRunStore(self.db_path)
        self.store.apply_migrations()
        self.replay = RunReplayEndpoint(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_end_to_end_orchestration_flow_is_replayable(self) -> None:
        context = self._create_context("Collect diagnostics")
        registry = RuntimeModuleRegistry(
            planner=DeterministicPlanner(),
            executor=ArtifactExecutor(),
            validator=PolicyAwareValidatorStage(),
            reporter=SummaryReporter(),
        )
        coordinator = RunCoordinator(registry)

        result = coordinator.run(context)
        final_status = self._persist_run_result(context.run_id, result)

        self.assertEqual(final_status, "completed")

        replay = self.replay.replay(context.run_id)
        event_types = [event.event_type for event in replay.events]
        self.assertEqual(
            event_types,
            [
                "runtime.plan.completed",
                "runtime.execute.completed",
                "runtime.validate.completed",
                "runtime.report.completed",
                "runtime.run.completed",
            ],
        )
        self.assertEqual(replay.run.status, "completed")
        self.assertEqual(replay.metadata["total_event_count"], 5)

    def test_policy_denial_propagates_to_failed_run(self) -> None:
        context = self._create_context("Attempt restricted operation")
        registry = RuntimeModuleRegistry(
            planner=DeterministicPlanner(),
            executor=DenyPolicyExecutor(),
            validator=PolicyAwareValidatorStage(),
            reporter=SummaryReporter(),
        )
        coordinator = RunCoordinator(registry)

        result = coordinator.run(context)
        final_status = self._persist_run_result(context.run_id, result)

        self.assertEqual(final_status, "failed")

        replay = self.replay.replay(context.run_id)
        validation_events = [event for event in replay.events if event.event_type == "runtime.validate.completed"]
        self.assertEqual(len(validation_events), 1)
        self.assertFalse(validation_events[0].payload["passed"])
        self.assertTrue(validation_events[0].payload["policy_violations"])

        status_events = [event for event in replay.events if event.event_type == "runtime.run.failed"]
        self.assertEqual(len(status_events), 1)

        run = self.store.get_run(context.run_id)
        self.assertEqual(run.status, "failed")

    def test_replay_digest_is_stable_and_contains_artifacts(self) -> None:
        context = self._create_context("Collect diagnostics")
        registry = RuntimeModuleRegistry(
            planner=DeterministicPlanner(),
            executor=ArtifactExecutor(),
            validator=PolicyAwareValidatorStage(),
            reporter=SummaryReporter(),
        )
        coordinator = RunCoordinator(registry)

        result = coordinator.run(context)
        self._persist_run_result(context.run_id, result)

        first = self.replay.replay(context.run_id)
        second = self.replay.replay(context.run_id)

        self.assertEqual(first.metadata["audit_digest"], second.metadata["audit_digest"])

        report_events = [event for event in first.events if event.event_type == "runtime.report.completed"]
        self.assertEqual(len(report_events), 1)
        self.assertEqual(report_events[0].payload["artifact_count"], 1)
        self.assertEqual(len(report_events[0].payload["artifacts"]), 1)

    def _create_context(self, goal: str, actor_id: str = "boss") -> RunContext:
        run_id = str(uuid4())
        self.store.create_run(run_id, goal, actor_id, status="created")
        return RunContext(run_id=run_id, goal=goal, actor_id=actor_id)

    def _persist_run_result(self, run_id: str, result) -> str:
        self.store.append_event(
            run_id,
            "runtime.plan.completed",
            {
                "plan_id": result.plan.plan_id,
                "task_count": len(result.plan.tasks),
            },
        )
        self.store.append_event(
            run_id,
            "runtime.execute.completed",
            {
                "execution_status": result.execution.status,
                "output_count": len(result.execution.outputs),
            },
        )
        self.store.append_event(
            run_id,
            "runtime.validate.completed",
            {
                "passed": result.validation.passed,
                "policy_violations": result.validation.details.get("policy_violations", []),
            },
            severity="info" if result.validation.passed else "warning",
        )
        self.store.append_event(
            run_id,
            "runtime.report.completed",
            {
                "report_id": result.report.report_id,
                "artifact_count": len(result.report.artifacts),
                "artifacts": result.report.artifacts,
            },
        )

        final_status = "completed" if result.execution.status == "success" and result.validation.passed else "failed"
        self.store.update_run_status(run_id, final_status)
        self.store.append_event(
            run_id,
            f"runtime.run.{final_status}",
            {
                "validation_passed": result.validation.passed,
                "execution_status": result.execution.status,
            },
            severity="info" if final_status == "completed" else "error",
        )

        return final_status


if __name__ == "__main__":
    unittest.main()
