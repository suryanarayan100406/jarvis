"""Tests for P8-T4 visual planner integration with runtime action stages."""

from __future__ import annotations

import unittest

from runtime.multimodal import (
    OCRLayoutAnalyzer,
    ScreenshotIngestionPipeline,
    UIGroundingModel,
    UIStateRepresentation,
    VisualActionPlanner,
    VisualPlannerError,
)
from runtime.pipeline.models import RunContext


class VisualActionPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ingestion = ScreenshotIngestionPipeline()
        self.ocr = OCRLayoutAnalyzer(min_confidence=0.35)
        self.grounding = UIGroundingModel(min_element_confidence=0.45)

    def test_plan_includes_runtime_stage_bindings_for_visual_actions(self) -> None:
        state = self._build_ui_state(
            [
                {
                    "text": "Deploy",
                    "left": 20,
                    "top": 30,
                    "width": 60,
                    "height": 18,
                    "confidence": 0.94,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Now",
                    "left": 86,
                    "top": 30,
                    "width": 40,
                    "height": 18,
                    "confidence": 0.93,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Status",
                    "left": 22,
                    "top": 64,
                    "width": 62,
                    "height": 18,
                    "confidence": 0.9,
                    "line_id": "line-2",
                    "block_id": "block-a",
                },
            ],
            candidates=[
                {
                    "role": "button",
                    "left": 14,
                    "top": 24,
                    "width": 132,
                    "height": 30,
                    "confidence": 0.96,
                    "selector_hints": ["#deploy-button"],
                }
            ],
        )

        planner = VisualActionPlanner(min_grounding_confidence=0.5, max_actions=2)
        context = RunContext(run_id="run-visual-1", goal="Deploy now and verify status", actor_id="boss")

        result = planner.plan(context, state)

        self.assertEqual(result.scene_id, state.scene_id)
        self.assertGreater(len(result.plan.tasks), 0)
        self.assertTrue(result.selected_element_ids)

        stage_map = result.plan.metadata["runtime_stage_task_map"]
        self.assertGreaterEqual(len(stage_map["plan"]), 1)
        self.assertGreaterEqual(len(stage_map["execute"]), 1)
        self.assertGreaterEqual(len(stage_map["validate"]), 1)
        self.assertEqual(stage_map["report"], [])

        action_bindings = [binding for binding in result.bindings if binding.action_stage == "ui_action"]
        self.assertGreaterEqual(len(action_bindings), 1)

    def test_plan_is_deterministic_for_same_goal_and_state(self) -> None:
        state = self._build_ui_state(
            [
                {
                    "text": "Open",
                    "left": 30,
                    "top": 40,
                    "width": 40,
                    "height": 18,
                    "confidence": 0.9,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Logs",
                    "left": 74,
                    "top": 40,
                    "width": 36,
                    "height": 18,
                    "confidence": 0.89,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
            ],
            candidates=[
                {
                    "role": "button",
                    "left": 24,
                    "top": 34,
                    "width": 96,
                    "height": 30,
                    "confidence": 0.92,
                }
            ],
        )

        planner = VisualActionPlanner(min_grounding_confidence=0.5, max_actions=1)
        context_a = RunContext(run_id="run-visual-a", goal="Open logs", actor_id="boss")
        context_b = RunContext(run_id="run-visual-b", goal="Open logs", actor_id="boss")

        result_a = planner.plan(context_a, state)
        result_b = planner.plan(context_b, state)

        self.assertEqual(result_a.plan.plan_id, result_b.plan.plan_id)
        self.assertEqual(result_a.plan.metadata["serialized_plan"], result_b.plan.metadata["serialized_plan"])

    def test_low_confidence_fallback_adds_warning_and_confirmation(self) -> None:
        state = self._build_ui_state(
            [
                {
                    "text": "Continue",
                    "left": 20,
                    "top": 30,
                    "width": 72,
                    "height": 18,
                    "confidence": 0.58,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
            candidates=[
                {
                    "role": "button",
                    "left": 16,
                    "top": 24,
                    "width": 96,
                    "height": 30,
                    "confidence": 0.62,
                }
            ],
        )

        planner = VisualActionPlanner(min_grounding_confidence=0.95, max_actions=1)
        context = RunContext(run_id="run-low-confidence", goal="Continue setup", actor_id="boss")

        result = planner.plan(context, state)

        self.assertTrue(any("fallback" in warning.lower() for warning in result.warnings))
        action_tasks = [task for task in result.plan.tasks if task.metadata.get("action_stage") == "ui_action"]
        self.assertEqual(len(action_tasks), 1)
        self.assertTrue(action_tasks[0].metadata["requires_confirmation"])

    def test_destructive_goal_marks_action_as_high_risk(self) -> None:
        state = self._build_ui_state(
            [
                {
                    "text": "Delete",
                    "left": 24,
                    "top": 34,
                    "width": 52,
                    "height": 18,
                    "confidence": 0.9,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Backup",
                    "left": 80,
                    "top": 34,
                    "width": 62,
                    "height": 18,
                    "confidence": 0.89,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
            ],
            candidates=[
                {
                    "role": "button",
                    "left": 18,
                    "top": 28,
                    "width": 132,
                    "height": 30,
                    "confidence": 0.93,
                }
            ],
        )

        planner = VisualActionPlanner(min_grounding_confidence=0.5, max_actions=1)
        context = RunContext(run_id="run-delete", goal="Delete backup", actor_id="boss")

        result = planner.plan(context, state)
        action_tasks = [task for task in result.plan.tasks if task.metadata.get("action_stage") == "ui_action"]

        self.assertEqual(len(action_tasks), 1)
        self.assertTrue(action_tasks[0].metadata["requires_confirmation"])
        self.assertEqual(action_tasks[0].metadata["risk_hint"], "high")

    def test_invalid_inputs_raise_errors(self) -> None:
        with self.assertRaises(VisualPlannerError):
            VisualActionPlanner(max_actions=0)

        planner = VisualActionPlanner()
        state = self._build_ui_state(
            [
                {
                    "text": "Ready",
                    "left": 10,
                    "top": 10,
                    "width": 42,
                    "height": 16,
                    "confidence": 0.9,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
            candidates=[],
        )

        with self.assertRaises(VisualPlannerError):
            planner.plan("not-a-context", state)  # type: ignore[arg-type]

    def _build_ui_state(
        self,
        ocr_payload: list[dict[str, object]],
        *,
        candidates: list[dict[str, object]],
    ) -> UIStateRepresentation:
        scene = self.ingestion.ingest_and_normalize_bytes(
            _fake_png(width=1920, height=1080),
            source_id="scene:visual-planner",
            source_type="desktop_capture",
        )
        layout = self.ocr.analyze_payload(scene, ocr_payload)
        return self.grounding.build_state(scene, layout, candidates=candidates)


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
