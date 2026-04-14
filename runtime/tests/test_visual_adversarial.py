"""Adversarial tests for deceptive UI patterns in multimodal workflows (P8-T11)."""

from __future__ import annotations

import unittest

from runtime.multimodal import (
    OCRLayoutAnalyzer,
    SafeUIActionExecutor,
    ScreenshotIngestionPipeline,
    UIActionExecutorError,
    UIGroundingModel,
    UIStateRepresentation,
    VisualActionPlanner,
    VisualConfidenceFallbackStrategy,
)
from runtime.pipeline.models import RunContext


class VisualAdversarialPatternTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ingestion = ScreenshotIngestionPipeline()
        self.ocr = OCRLayoutAnalyzer(min_confidence=0.35)
        self.grounding = UIGroundingModel(min_element_confidence=0.45)
        self.executor = SafeUIActionExecutor(min_precheck_confidence=0.5)

    def test_invisible_overlay_decoy_is_not_selected_for_action(self) -> None:
        state = self._build_ui_state(
            [
                {
                    "text": "Open Logs",
                    "left": 30,
                    "top": 32,
                    "width": 90,
                    "height": 18,
                    "confidence": 0.93,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Apply Update",
                    "left": 30,
                    "top": 74,
                    "width": 120,
                    "height": 18,
                    "confidence": 0.91,
                    "line_id": "line-2",
                    "block_id": "block-b",
                },
            ],
            candidates=[
                {
                    "role": "button",
                    "label": "Apply Update",
                    "left": 26,
                    "top": 66,
                    "width": 160,
                    "height": 34,
                    "confidence": 0.99,
                    "attributes": {"visible": False},
                },
                {
                    "role": "button",
                    "label": "Open Logs",
                    "left": 24,
                    "top": 24,
                    "width": 140,
                    "height": 34,
                    "confidence": 0.84,
                },
            ],
            source_id="scene:overlay-decoy",
        )
        planner = VisualActionPlanner(min_grounding_confidence=0.5, max_actions=1)
        context = RunContext(run_id="run-overlay-decoy", goal="Open logs", actor_id="boss")

        result = planner.plan(context, state)

        action_tasks = [task for task in result.plan.tasks if task.metadata.get("action_stage") == "ui_action"]
        self.assertEqual(len(action_tasks), 1)
        self.assertEqual(action_tasks[0].metadata["element_label"], "Open Logs")
        self.assertFalse(action_tasks[0].metadata["requires_confirmation"])

    def test_spoofed_destructive_label_requires_confirmation_for_safe_goal(self) -> None:
        state = self._build_ui_state(
            [
                {
                    "text": "Disable Antivirus",
                    "left": 26,
                    "top": 30,
                    "width": 150,
                    "height": 18,
                    "confidence": 0.94,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
            candidates=[
                {
                    "role": "button",
                    "label": "Disable Antivirus",
                    "left": 22,
                    "top": 24,
                    "width": 190,
                    "height": 34,
                    "confidence": 0.95,
                }
            ],
            source_id="scene:spoofed-destructive-label",
        )
        planner = VisualActionPlanner(min_grounding_confidence=0.5, max_actions=1)
        context = RunContext(run_id="run-spoofed-destructive", goal="Open dashboard", actor_id="boss")

        result = planner.plan(context, state)

        action_tasks = [task for task in result.plan.tasks if task.metadata.get("action_stage") == "ui_action"]
        self.assertEqual(len(action_tasks), 1)
        self.assertTrue(action_tasks[0].metadata["requires_confirmation"])
        self.assertEqual(action_tasks[0].metadata["risk_hint"], "high")

    def test_deceptive_low_confidence_target_is_deferred_and_blocked(self) -> None:
        state = self._build_ui_state(
            [
                {
                    "text": "Proceed",
                    "left": 26,
                    "top": 30,
                    "width": 60,
                    "height": 18,
                    "confidence": 0.91,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
            candidates=[
                {
                    "role": "button",
                    "label": "Proceed",
                    "left": 22,
                    "top": 24,
                    "width": 120,
                    "height": 34,
                    "confidence": 0.3,
                }
            ],
            source_id="scene:defer-low-confidence",
        )
        planner = VisualActionPlanner(
            min_grounding_confidence=0.5,
            max_actions=1,
            fallback_strategy=VisualConfidenceFallbackStrategy(
                min_autonomous_confidence=0.8,
                min_confirmation_confidence=0.7,
            ),
        )
        context = RunContext(run_id="run-defer-adversarial", goal="Proceed to next page", actor_id="boss")

        planning = planner.plan(context, state)
        action_tasks = [task for task in planning.plan.tasks if task.metadata.get("action_stage") == "ui_action"]
        self.assertEqual(len(action_tasks), 1)
        self.assertEqual(action_tasks[0].metadata["fallback_mode"], "defer")
        self.assertEqual(action_tasks[0].metadata["risk_hint"], "critical")

        execution = self.executor.execute(context, planning.plan, state)
        self.assertEqual(execution.execution.status, "blocked")
        precheck_outputs = [entry for entry in execution.execution.outputs if entry["action_stage"] == "ui_precheck"]
        self.assertEqual(len(precheck_outputs), 1)
        self.assertEqual(precheck_outputs[0]["status"], "blocked")
        self.assertEqual(precheck_outputs[0]["fallback_mode"], "defer")

    def test_scene_replay_attack_is_rejected(self) -> None:
        planner = VisualActionPlanner(min_grounding_confidence=0.5, max_actions=1)
        context = RunContext(run_id="run-scene-replay", goal="Open logs", actor_id="boss")

        trusted_state = self._build_ui_state(
            [
                {
                    "text": "Open Logs",
                    "left": 24,
                    "top": 30,
                    "width": 92,
                    "height": 18,
                    "confidence": 0.93,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
            candidates=[
                {
                    "role": "button",
                    "label": "Open Logs",
                    "left": 20,
                    "top": 24,
                    "width": 140,
                    "height": 34,
                    "confidence": 0.9,
                }
            ],
            source_id="scene:trusted",
        )
        replay_state = self._build_ui_state(
            [
                {
                    "text": "Open Logs",
                    "left": 24,
                    "top": 30,
                    "width": 92,
                    "height": 18,
                    "confidence": 0.93,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
            candidates=[
                {
                    "role": "button",
                    "label": "Open Logs",
                    "left": 20,
                    "top": 24,
                    "width": 140,
                    "height": 34,
                    "confidence": 0.9,
                }
            ],
            source_id="scene:replay",
        )

        plan = planner.plan(context, trusted_state).plan

        with self.assertRaises(UIActionExecutorError):
            self.executor.execute(context, plan, replay_state)

    def _build_ui_state(
        self,
        ocr_payload: list[dict[str, object]],
        *,
        candidates: list[dict[str, object]],
        source_id: str,
    ) -> UIStateRepresentation:
        scene = self.ingestion.ingest_and_normalize_bytes(
            _fake_png(width=1920, height=1080),
            source_id=source_id,
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
