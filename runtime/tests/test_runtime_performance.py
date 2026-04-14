"""Performance tests for P2-T12 startup and latency budgets."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from time import perf_counter

from runtime.cli import run_cli
from runtime.pipeline import (
    DeterministicPlanner,
    EchoExecutor,
    PassValidator,
    RunCoordinator,
    RuntimeModuleRegistry,
    SummaryReporter,
)
from runtime.planner import PlannerInterfaceAdapter
from runtime.store import LocalRunStore

STARTUP_BUDGET_SECONDS = 1.5
PLAN_P95_BUDGET_SECONDS = 0.03
SUBMIT_ACK_BUDGET_SECONDS = 1.5


class RuntimePerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runs.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_runtime_startup_is_within_budget(self) -> None:
        start = perf_counter()

        store = LocalRunStore(self.db_path)
        store.apply_migrations()

        registry = RuntimeModuleRegistry(
            planner=DeterministicPlanner(),
            executor=EchoExecutor(),
            validator=PassValidator(),
            reporter=SummaryReporter(),
        )
        RunCoordinator(registry)

        elapsed = perf_counter() - start
        self.assertLess(
            elapsed,
            STARTUP_BUDGET_SECONDS,
            msg=(
                f"Runtime startup exceeded budget: elapsed={elapsed:.6f}s "
                f"budget={STARTUP_BUDGET_SECONDS:.6f}s"
            ),
        )

    def test_planner_p95_latency_is_within_budget(self) -> None:
        planner = PlannerInterfaceAdapter()
        samples: list[float] = []

        for index in range(40):
            start = perf_counter()
            planner.build_plan_payload(
                goal="Collect diagnostics and summarize metrics",
                constraints={"iteration": index, "priority": "high"},
            )
            samples.append(perf_counter() - start)

        p95 = _percentile(samples, 95)
        self.assertLess(
            p95,
            PLAN_P95_BUDGET_SECONDS,
            msg=(
                f"Planner latency exceeded budget: p95={p95:.6f}s "
                f"budget={PLAN_P95_BUDGET_SECONDS:.6f}s"
            ),
        )

    def test_submit_acknowledgement_is_within_budget(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        start = perf_counter()
        exit_code = run_cli(
            [
                "submit",
                "--goal",
                "Collect diagnostics",
                "--actor-id",
                "boss",
                "--run-id",
                "perf-run-1",
            ],
            db_path=self.db_path,
            stdout=stdout,
            stderr=stderr,
        )
        elapsed = perf_counter() - start

        self.assertEqual(exit_code, 0, msg=f"Submit command failed: stderr={stderr.getvalue().strip()}")
        self.assertLess(
            elapsed,
            SUBMIT_ACK_BUDGET_SECONDS,
            msg=(
                f"Submit acknowledgement exceeded budget: elapsed={elapsed:.6f}s "
                f"budget={SUBMIT_ACK_BUDGET_SECONDS:.6f}s"
            ),
        )


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        raise ValueError("values cannot be empty")
    if percentile < 0 or percentile > 100:
        raise ValueError("percentile must be in range 0..100")

    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * (percentile / 100)))
    return ordered[index]


if __name__ == "__main__":
    unittest.main()
