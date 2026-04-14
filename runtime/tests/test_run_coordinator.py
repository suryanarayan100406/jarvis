"""Tests for P2-T2 deterministic run coordinator."""

from __future__ import annotations

import unittest

from runtime.control import KillSwitchController
from runtime.pipeline import (
    DeterministicPlanner,
    EchoExecutor,
    PassValidator,
    RuntimeModuleRegistry,
    SummaryReporter,
    new_run_context,
)
from runtime.pipeline.coordinator import RunCoordinator, RunCoordinatorError


class FailingExecutor:
    def execute(self, _context, _plan):
        raise RuntimeError("execution_failure")


class RunCoordinatorTests(unittest.TestCase):
    def test_happy_path_transitions_are_deterministic(self) -> None:
        registry = RuntimeModuleRegistry(
            planner=DeterministicPlanner(),
            executor=EchoExecutor(),
            validator=PassValidator(),
            reporter=SummaryReporter(),
        )
        coordinator = RunCoordinator(registry)
        context = new_run_context(goal="Collect diagnostics", actor_id="boss")

        result = coordinator.run(context)

        transitions = [(t.from_stage, t.to_stage) for t in result.transitions]
        self.assertEqual(
            transitions,
            [
                ("plan", "execute"),
                ("execute", "validate"),
                ("validate", "report"),
                ("report", "completed"),
            ],
        )

    def test_failure_transitions_to_failed_stage(self) -> None:
        registry = RuntimeModuleRegistry(
            planner=DeterministicPlanner(),
            executor=FailingExecutor(),
            validator=PassValidator(),
            reporter=SummaryReporter(),
        )
        coordinator = RunCoordinator(registry)
        context = new_run_context(goal="Collect diagnostics", actor_id="boss")

        with self.assertRaises(RunCoordinatorError) as error:
            coordinator.run(context)

        self.assertEqual(error.exception.stage, "failed")
        self.assertEqual(error.exception.transitions[-1].to_stage, "failed")

    def test_kill_switch_blocks_execution(self) -> None:
        registry = RuntimeModuleRegistry(
            planner=DeterministicPlanner(),
            executor=EchoExecutor(),
            validator=PassValidator(),
            reporter=SummaryReporter(),
        )
        kill_switch = KillSwitchController()
        kill_switch.activate(reason="emergency_stop", actor="boss")
        coordinator = RunCoordinator(registry, kill_switch=kill_switch)
        context = new_run_context(goal="Collect diagnostics", actor_id="boss")

        with self.assertRaises(RunCoordinatorError) as error:
            coordinator.run(context)

        self.assertEqual(error.exception.stage, "failed")
        self.assertEqual(error.exception.transitions[-1].to_stage, "failed")


if __name__ == "__main__":
    unittest.main()
