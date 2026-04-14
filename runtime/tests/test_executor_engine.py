"""Tests for P2-T4 executor engine retry, timeout, and cancellation behavior."""

from __future__ import annotations

import time
import unittest

from runtime.executor import ExecutorEngine
from runtime.pipeline.models import PlanResult, PlannedTask, RunContext


def _build_plan() -> PlanResult:
    return PlanResult(
        plan_id="plan-001",
        tasks=[
            PlannedTask(task_id="task-1", description="first"),
            PlannedTask(task_id="task-2", description="second"),
        ],
    )


class ExecutorEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = RunContext(run_id="run-exec", goal="Execute tasks", actor_id="boss")

    def test_successful_execution(self) -> None:
        engine = ExecutorEngine(timeout_seconds=0.2, max_attempts=1)

        result = engine.execute(self.context, _build_plan())

        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.outputs), 2)

    def test_retry_then_success(self) -> None:
        attempts = {"count": 0}
        retries: list[tuple[str, int, str]] = []

        def flaky_runner(task, _context):
            if task.task_id == "task-1" and attempts["count"] == 0:
                attempts["count"] += 1
                raise RuntimeError("transient_failure")
            return {"ok": task.task_id}

        engine = ExecutorEngine(
            task_runner=flaky_runner,
            timeout_seconds=0.2,
            max_attempts=2,
            on_retry=lambda task_id, attempt, reason: retries.append((task_id, attempt, reason)),
        )

        result = engine.execute(self.context, _build_plan())

        self.assertEqual(result.status, "success")
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0][0], "task-1")

    def test_timeout_calls_hook_and_returns_timeout_status(self) -> None:
        timeouts: list[tuple[str, int]] = []

        def slow_runner(_task, _context):
            time.sleep(0.05)
            return {"ok": True}

        engine = ExecutorEngine(
            task_runner=slow_runner,
            timeout_seconds=0.01,
            max_attempts=1,
            on_timeout=lambda task_id, attempt: timeouts.append((task_id, attempt)),
        )

        result = engine.execute(self.context, _build_plan())

        self.assertEqual(result.status, "timeout")
        self.assertEqual(len(timeouts), 1)

    def test_cancellation_stops_execution(self) -> None:
        cancelled: list[str] = []
        engine = ExecutorEngine(
            timeout_seconds=0.2,
            max_attempts=1,
            cancellation_checker=lambda: True,
            on_cancel=lambda task_id: cancelled.append(task_id),
        )

        result = engine.execute(self.context, _build_plan())

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(len(result.outputs), 0)
        self.assertEqual(cancelled, ["task-1"])

    def test_failure_after_retries_returns_failed(self) -> None:
        def always_fail(_task, _context):
            raise RuntimeError("permanent_failure")

        engine = ExecutorEngine(task_runner=always_fail, timeout_seconds=0.2, max_attempts=2)

        result = engine.execute(self.context, _build_plan())

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.outputs[0]["status"], "failed")
        self.assertEqual(result.outputs[0]["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
