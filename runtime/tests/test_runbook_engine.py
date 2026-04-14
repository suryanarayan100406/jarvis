"""Tests for P6-T3 runbook execution engine."""

from __future__ import annotations

import time
import unittest

from runtime.orchestration import (
    RunbookEngineError,
    RunbookExecutionEngine,
    RunbookStep,
)


class RunbookExecutionEngineTests(unittest.TestCase):
    def test_register_and_execute_runbook_successfully(self) -> None:
        def collect(step: RunbookStep, _context: dict) -> dict:
            return {"step": step.step_id, "action": "collect"}

        def restart(step: RunbookStep, _context: dict) -> dict:
            return {"service": step.parameters.get("service", "unknown"), "status": "restarted"}

        engine = RunbookExecutionEngine(
            handlers={
                "collect_metrics": collect,
                "restart_service": restart,
            }
        )
        engine.register_runbook(
            runbook_id="rb-ops",
            name="Ops routine",
            steps=[
                RunbookStep(step_id="s1", action="collect_metrics", parameters={}),
                RunbookStep(step_id="s2", action="restart_service", parameters={"service": "api"}),
            ],
        )

        result = engine.execute_runbook("rb-ops", context={"trigger": "schedule"})

        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.step_results), 2)
        self.assertEqual(result.step_results[0].status, "success")
        self.assertEqual(result.step_results[1].output["service"], "api")

    def test_step_retry_then_success(self) -> None:
        attempts = {"count": 0}

        def flaky(_step: RunbookStep, _context: dict) -> str:
            if attempts["count"] == 0:
                attempts["count"] += 1
                raise RuntimeError("transient")
            return "ok"

        engine = RunbookExecutionEngine(handlers={"flaky": flaky})
        engine.register_runbook(
            runbook_id="rb-retry",
            name="Retry routine",
            steps=[RunbookStep(step_id="s1", action="flaky", parameters={}, max_attempts=2)],
        )

        result = engine.execute_runbook("rb-retry")

        self.assertEqual(result.status, "success")
        self.assertEqual(result.step_results[0].attempt_count, 2)

    def test_missing_handler_fails_runbook(self) -> None:
        engine = RunbookExecutionEngine(handlers={})
        engine.register_runbook(
            runbook_id="rb-missing",
            name="Missing handler",
            steps=[RunbookStep(step_id="s1", action="unknown_action", parameters={})],
        )

        result = engine.execute_runbook("rb-missing")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.step_results[0].status, "failed")
        self.assertIn("No handler registered", result.step_results[0].error)

    def test_continue_on_failure_results_in_degraded_status(self) -> None:
        def ok(_step: RunbookStep, _context: dict) -> str:
            return "ok"

        engine = RunbookExecutionEngine(handlers={"ok": ok})
        engine.register_runbook(
            runbook_id="rb-degraded",
            name="Degraded flow",
            steps=[
                RunbookStep(
                    step_id="s1",
                    action="missing",
                    parameters={},
                    continue_on_failure=True,
                ),
                RunbookStep(step_id="s2", action="ok", parameters={}),
            ],
        )

        result = engine.execute_runbook("rb-degraded")

        self.assertEqual(result.status, "degraded")
        self.assertEqual(len(result.step_results), 2)
        self.assertEqual(result.step_results[1].status, "success")

    def test_timeout_returns_timeout_status(self) -> None:
        def slow(_step: RunbookStep, _context: dict) -> str:
            time.sleep(0.05)
            return "done"

        engine = RunbookExecutionEngine(handlers={"slow": slow}, default_timeout_seconds=0.01)
        engine.register_runbook(
            runbook_id="rb-timeout",
            name="Timeout flow",
            steps=[RunbookStep(step_id="s1", action="slow", parameters={})],
        )

        result = engine.execute_runbook("rb-timeout")

        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.step_results[0].status, "timeout")

    def test_duplicate_runbook_registration_raises(self) -> None:
        engine = RunbookExecutionEngine(handlers={"ok": lambda _step, _context: "ok"})
        engine.register_runbook(
            runbook_id="rb-dup",
            name="First",
            steps=[RunbookStep(step_id="s1", action="ok", parameters={})],
        )

        with self.assertRaises(RunbookEngineError):
            engine.register_runbook(
                runbook_id="rb-dup",
                name="Second",
                steps=[RunbookStep(step_id="s2", action="ok", parameters={})],
            )

    def test_fallback_plan_recovers_failed_step(self) -> None:
        def primary_fail(_step: RunbookStep, _context: dict) -> str:
            raise RuntimeError("primary failure")

        def fallback_ok(step: RunbookStep, _context: dict) -> dict:
            return {"fallback": step.parameters.get("mode")}

        engine = RunbookExecutionEngine(
            handlers={
                "primary": primary_fail,
                "fallback": fallback_ok,
            }
        )
        engine.register_runbook(
            runbook_id="rb-fallback",
            name="Fallback flow",
            steps=[
                RunbookStep(
                    step_id="s1",
                    action="primary",
                    parameters={},
                    fallback_action="fallback",
                    fallback_parameters={"mode": "safe"},
                )
            ],
        )

        result = engine.execute_runbook("rb-fallback")

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.step_results[0].status, "fallback_succeeded")
        self.assertEqual(result.step_results[0].fallback_status, "success")
        self.assertEqual(result.step_results[0].output["fallback"], "safe")
        self.assertEqual(result.metrics["steps_fallback_used"], 1)

    def test_fallback_failure_preserves_primary_failure_status(self) -> None:
        def primary_fail(_step: RunbookStep, _context: dict) -> str:
            raise RuntimeError("primary failure")

        def fallback_fail(_step: RunbookStep, _context: dict) -> str:
            raise RuntimeError("fallback failure")

        engine = RunbookExecutionEngine(
            handlers={
                "primary": primary_fail,
                "fallback": fallback_fail,
            }
        )
        engine.register_runbook(
            runbook_id="rb-fallback-fail",
            name="Fallback failure flow",
            steps=[
                RunbookStep(
                    step_id="s1",
                    action="primary",
                    parameters={},
                    fallback_action="fallback",
                )
            ],
        )

        result = engine.execute_runbook("rb-fallback-fail")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.step_results[0].status, "failed")
        self.assertEqual(result.step_results[0].fallback_status, "failed")
        self.assertIn("fallback failure", result.step_results[0].fallback_error)


if __name__ == "__main__":
    unittest.main()
