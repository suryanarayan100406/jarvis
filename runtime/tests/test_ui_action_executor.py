"""Tests for P8-T5 safe UI action executor with confirmation checkpoints."""

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
)
from runtime.pipeline.models import RunContext


class SafeUIActionExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ingestion = ScreenshotIngestionPipeline()
        self.ocr = OCRLayoutAnalyzer(min_confidence=0.35)
        self.grounding = UIGroundingModel(min_element_confidence=0.45)
        self.planner = VisualActionPlanner(min_grounding_confidence=0.5, max_actions=1)
        self.executor = SafeUIActionExecutor(min_precheck_confidence=0.5)

    def test_non_destructive_visual_action_executes_without_checkpoint(self) -> None:
        context, plan, state = self._build_visual_plan(
            goal="Open settings",
            ocr_payload=[
                {
                    "text": "Open",
                    "left": 20,
                    "top": 30,
                    "width": 40,
                    "height": 18,
                    "confidence": 0.94,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Settings",
                    "left": 64,
                    "top": 30,
                    "width": 72,
                    "height": 18,
                    "confidence": 0.93,
                    "line_id": "line-1",
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
                    "selector_hints": ["#open-settings"],
                }
            ],
        )

        result = self.executor.execute(context, plan, state)

        self.assertEqual(result.execution.status, "success")
        self.assertEqual(result.checkpoints, ())
        ui_action_outputs = [entry for entry in result.execution.outputs if entry["action_stage"] == "ui_action"]
        self.assertEqual(len(ui_action_outputs), 1)
        self.assertEqual(ui_action_outputs[0]["status"], "success")

    def test_risky_visual_action_requires_confirmation_checkpoint(self) -> None:
        context, plan, state = self._build_visual_plan(
            goal="Delete backup",
            ocr_payload=[
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
                    "left": 82,
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
                    "width": 134,
                    "height": 30,
                    "confidence": 0.94,
                }
            ],
        )

        result = self.executor.execute(context, plan, state)

        self.assertEqual(result.execution.status, "awaiting_confirmation")
        self.assertEqual(len(result.checkpoints), 1)
        ui_action_outputs = [entry for entry in result.execution.outputs if entry["action_stage"] == "ui_action"]
        self.assertEqual(len(ui_action_outputs), 1)
        self.assertEqual(ui_action_outputs[0]["status"], "awaiting_confirmation")
        self.assertIn("checkpoint_token", ui_action_outputs[0])

    def test_valid_checkpoint_token_allows_risky_action_execution(self) -> None:
        context, plan, state = self._build_visual_plan(
            goal="Delete backup",
            ocr_payload=[
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
                    "left": 82,
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
                    "width": 134,
                    "height": 30,
                    "confidence": 0.94,
                }
            ],
        )

        pending = self.executor.execute(context, plan, state)
        self.assertEqual(pending.execution.status, "awaiting_confirmation")
        checkpoint = pending.checkpoints[0]
        action_task_id = checkpoint.task_id

        confirmed = self.executor.execute(
            context,
            plan,
            state,
            confirmation_tokens={action_task_id: checkpoint.token},
        )

        self.assertEqual(confirmed.execution.status, "success")
        ui_action_outputs = [entry for entry in confirmed.execution.outputs if entry["action_stage"] == "ui_action"]
        self.assertEqual(len(ui_action_outputs), 1)
        self.assertEqual(ui_action_outputs[0]["status"], "success")

    def test_reusing_consumed_checkpoint_token_is_blocked(self) -> None:
        context, plan, state = self._build_visual_plan(
            goal="Delete backup",
            ocr_payload=[
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
                    "left": 82,
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
                    "width": 134,
                    "height": 30,
                    "confidence": 0.94,
                }
            ],
        )

        pending = self.executor.execute(context, plan, state)
        checkpoint = pending.checkpoints[0]
        action_task_id = checkpoint.task_id

        first_confirmed = self.executor.execute(
            context,
            plan,
            state,
            confirmation_tokens={action_task_id: checkpoint.token},
        )
        self.assertEqual(first_confirmed.execution.status, "success")

        replay = self.executor.execute(
            context,
            plan,
            state,
            confirmation_tokens={action_task_id: checkpoint.token},
        )
        self.assertEqual(replay.execution.status, "blocked")
        ui_action_outputs = [entry for entry in replay.execution.outputs if entry["action_stage"] == "ui_action"]
        self.assertEqual(len(ui_action_outputs), 1)
        self.assertEqual(ui_action_outputs[0]["status"], "blocked")

    def test_disabled_element_is_blocked_during_precheck(self) -> None:
        context, plan, state = self._build_visual_plan(
            goal="Open settings",
            ocr_payload=[
                {
                    "text": "Open",
                    "left": 20,
                    "top": 30,
                    "width": 40,
                    "height": 18,
                    "confidence": 0.94,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Settings",
                    "left": 64,
                    "top": 30,
                    "width": 72,
                    "height": 18,
                    "confidence": 0.93,
                    "line_id": "line-1",
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
                    "attributes": {"enabled": False},
                }
            ],
        )

        result = self.executor.execute(context, plan, state)

        self.assertEqual(result.execution.status, "blocked")
        precheck_outputs = [entry for entry in result.execution.outputs if entry["action_stage"] == "ui_precheck"]
        self.assertEqual(len(precheck_outputs), 1)
        self.assertEqual(precheck_outputs[0]["status"], "blocked")

    def test_scene_mismatch_between_plan_and_ui_state_raises_error(self) -> None:
        context, plan, _state = self._build_visual_plan(
            goal="Open logs",
            ocr_payload=[
                {
                    "text": "Open Logs",
                    "left": 20,
                    "top": 30,
                    "width": 84,
                    "height": 18,
                    "confidence": 0.94,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
            candidates=[
                {
                    "role": "button",
                    "left": 14,
                    "top": 24,
                    "width": 110,
                    "height": 30,
                    "confidence": 0.96,
                }
            ],
        )

        mismatched_state = self._build_ui_state(
            [
                {
                    "text": "Other",
                    "left": 10,
                    "top": 10,
                    "width": 40,
                    "height": 16,
                    "confidence": 0.9,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
            candidates=[],
            source_id="scene:mismatch",
        )

        with self.assertRaises(UIActionExecutorError):
            self.executor.execute(context, plan, mismatched_state)

    def _build_visual_plan(
        self,
        *,
        goal: str,
        ocr_payload: list[dict[str, object]],
        candidates: list[dict[str, object]],
    ) -> tuple[RunContext, object, UIStateRepresentation]:
        state = self._build_ui_state(ocr_payload, candidates=candidates, source_id="scene:ui-action")
        context = RunContext(run_id=_hash(goal), goal=goal, actor_id="boss")
        planning = self.planner.plan(context, state)
        return context, planning.plan, state

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


def _hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


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
