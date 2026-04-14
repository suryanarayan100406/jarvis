"""Performance tests for P8-T12 visual processing latency budgets."""

from __future__ import annotations

import unittest
from time import perf_counter

from runtime.multimodal import (
    OCRLayoutAnalyzer,
    SafeUIActionExecutor,
    ScreenshotIngestionPipeline,
    UIGroundingModel,
    VisualActionPlanner,
)
from runtime.pipeline.models import RunContext

SCREENSHOT_TO_PLAN_P95_BUDGET_SECONDS = 0.035
BATCHED_VISUAL_MIN_THROUGHPUT_TASKS_PER_SECOND = 80.0


class VisualProcessingPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ingestion = ScreenshotIngestionPipeline()
        self.ocr = OCRLayoutAnalyzer(min_confidence=0.35)
        self.grounding = UIGroundingModel(min_element_confidence=0.45)
        self.planner = VisualActionPlanner(min_grounding_confidence=0.5, max_actions=1)
        self.executor = SafeUIActionExecutor(min_precheck_confidence=0.5)

    def test_screenshot_to_plan_p95_latency_is_within_budget(self) -> None:
        samples: list[float] = []

        for index in range(50):
            start = perf_counter()
            scene = self.ingestion.ingest_and_normalize_bytes(
                _fake_png(width=1920 + (index % 3), height=1080),
                source_id=f"scene:perf-plan:{index}",
                source_type="desktop_capture",
            )
            layout = self.ocr.analyze_payload(scene, _ocr_payload(index), language_hint="en")
            ui_state = self.grounding.build_state(scene, layout, candidates=_candidates(index))
            context = RunContext(run_id=f"run-perf-plan-{index}", goal="Open logs", actor_id="boss")
            planning = self.planner.plan(context, ui_state)
            samples.append(perf_counter() - start)

            self.assertTrue(any(task.metadata.get("action_stage") == "ui_action" for task in planning.plan.tasks))

        p95 = _percentile(samples, 95)
        self.assertLess(
            p95,
            SCREENSHOT_TO_PLAN_P95_BUDGET_SECONDS,
            msg=(
                f"Screenshot-to-plan latency exceeded budget: p95={p95:.6f}s "
                f"budget={SCREENSHOT_TO_PLAN_P95_BUDGET_SECONDS:.6f}s"
            ),
        )

    def test_batched_visual_task_throughput_is_within_budget(self) -> None:
        batch_size = 60
        success_runs = 0

        start_total = perf_counter()
        for index in range(batch_size):
            scene = self.ingestion.ingest_and_normalize_bytes(
                _fake_png(width=1366 + (index % 5), height=768),
                source_id=f"scene:perf-batch:{index}",
                source_type="browser_capture",
            )
            layout = self.ocr.analyze_payload(scene, _ocr_payload(index), language_hint="en")
            ui_state = self.grounding.build_state(scene, layout, candidates=_candidates(index))
            context = RunContext(run_id=f"run-perf-batch-{index}", goal="Open logs", actor_id="boss")

            planning = self.planner.plan(context, ui_state)
            execution = self.executor.execute(context, planning.plan, ui_state)
            if execution.execution.status == "success":
                success_runs += 1

        elapsed = perf_counter() - start_total
        throughput = batch_size / elapsed if elapsed > 0 else float("inf")

        self.assertEqual(success_runs, batch_size)
        self.assertGreaterEqual(
            throughput,
            BATCHED_VISUAL_MIN_THROUGHPUT_TASKS_PER_SECOND,
            msg=(
                f"Batched visual throughput dropped below budget: throughput={throughput:.2f} tasks/s "
                f"budget={BATCHED_VISUAL_MIN_THROUGHPUT_TASKS_PER_SECOND:.2f} tasks/s"
            ),
        )



def _ocr_payload(index: int) -> list[dict[str, object]]:
    return [
        {
            "text": "System Dashboard",
            "left": 24,
            "top": 20,
            "width": 170,
            "height": 20,
            "confidence": 0.95,
            "line_id": f"line-a-{index}",
            "block_id": f"block-a-{index}",
        },
        {
            "text": "Open Logs",
            "left": 28,
            "top": 64,
            "width": 92,
            "height": 18,
            "confidence": 0.93,
            "line_id": f"line-b-{index}",
            "block_id": f"block-b-{index}",
        },
    ]



def _candidates(index: int) -> list[dict[str, object]]:
    return [
        {
            "role": "button",
            "label": "Open Logs",
            "left": 22,
            "top": 56,
            "width": 140,
            "height": 34,
            "confidence": 0.9,
            "selector_hints": [f"#open-logs-{index}"],
        }
    ]



def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        raise ValueError("values cannot be empty")
    if percentile < 0 or percentile > 100:
        raise ValueError("percentile must be in range 0..100")

    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * (percentile / 100)))
    return ordered[index]



def _fake_png(*, width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


if __name__ == "__main__":
    unittest.main()
