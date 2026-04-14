"""Tests for P2-T1 runtime module boundaries."""

from __future__ import annotations

import unittest

from runtime.pipeline import (
    DeterministicPlanner,
    EchoExecutor,
    ModuleBoundaryError,
    PassValidator,
    RuntimeModuleRegistry,
    SummaryReporter,
    new_run_context,
)


class ModuleBoundaryTests(unittest.TestCase):
    def test_registry_detects_missing_boundaries(self) -> None:
        registry = RuntimeModuleRegistry()

        with self.assertRaises(ModuleBoundaryError):
            registry.validate_boundaries()

    def test_registry_accepts_complete_boundaries(self) -> None:
        registry = RuntimeModuleRegistry(
            planner=DeterministicPlanner(),
            executor=EchoExecutor(),
            validator=PassValidator(),
            reporter=SummaryReporter(),
        )

        registry.validate_boundaries()

    def test_default_boundaries_run_pipeline(self) -> None:
        registry = RuntimeModuleRegistry(
            planner=DeterministicPlanner(),
            executor=EchoExecutor(),
            validator=PassValidator(),
            reporter=SummaryReporter(),
        )
        context = new_run_context(goal="Check server health", actor_id="boss")

        report = registry.run_pipeline(context)

        self.assertIn("execution=success", report.summary)
        self.assertIn("validation=True", report.summary)

    def test_planner_is_deterministic_for_same_goal(self) -> None:
        planner = DeterministicPlanner()
        context_a = new_run_context(goal="Generate diagnostics", actor_id="boss")
        context_b = new_run_context(goal="Generate diagnostics", actor_id="boss")

        plan_a = planner.plan(context_a)
        plan_b = planner.plan(context_b)

        self.assertEqual(plan_a.plan_id, plan_b.plan_id)


if __name__ == "__main__":
    unittest.main()
