"""Regression tests for common desktop and browser multimodal workflows (P8-T10)."""

from __future__ import annotations

import unittest

from runtime.memory import MemoryIndexingPipeline, MemoryRetrievalEngine
from runtime.multimodal import (
    DocumentImageSummaryExtractor,
    MultimodalEvidenceStore,
    OCRLayoutAnalyzer,
    SafeUIActionExecutor,
    ScreenshotIngestionPipeline,
    UIGroundingModel,
    VisualActionPlanner,
)
from runtime.pipeline.models import RunContext


class VisualWorkflowRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ingestion = ScreenshotIngestionPipeline()
        self.ocr = OCRLayoutAnalyzer(min_confidence=0.35)
        self.grounding = UIGroundingModel(min_element_confidence=0.45)
        self.planner = VisualActionPlanner(min_grounding_confidence=0.5, max_actions=2)
        self.executor = SafeUIActionExecutor(min_precheck_confidence=0.5)

        self.extractor = DocumentImageSummaryExtractor()
        self.index = MemoryIndexingPipeline()
        self.evidence_store = MultimodalEvidenceStore(self.index, default_namespace="multimodal")
        self.retrieval = MemoryRetrievalEngine(self.index)

    def test_desktop_settings_workflow_executes_end_to_end(self) -> None:
        scene = self.ingestion.ingest_and_normalize_bytes(
            _fake_png(width=1920, height=1080),
            source_id="scene:desktop-settings",
            source_type="desktop_capture",
        )
        layout = self.ocr.analyze_payload(
            scene,
            [
                {
                    "text": "System Settings",
                    "left": 28,
                    "top": 22,
                    "width": 180,
                    "height": 20,
                    "confidence": 0.95,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Open Settings",
                    "left": 36,
                    "top": 72,
                    "width": 140,
                    "height": 18,
                    "confidence": 0.92,
                    "line_id": "line-2",
                    "block_id": "block-a",
                },
            ],
            language_hint="en",
        )
        ui_state = self.grounding.build_state(
            scene,
            layout,
            candidates=[
                {
                    "role": "button",
                    "label": "Open Settings",
                    "left": 30,
                    "top": 64,
                    "width": 160,
                    "height": 34,
                    "confidence": 0.94,
                    "selector_hints": ["#open-settings"],
                }
            ],
        )

        context = RunContext(
            run_id="run-desktop-regression",
            goal="Open settings",
            actor_id="boss",
        )
        plan_result = self.planner.plan(context, ui_state)
        execution = self.executor.execute(context, plan_result.plan, ui_state)

        self.assertEqual(execution.execution.status, "success")
        self.assertEqual(execution.checkpoints, ())
        self.assertTrue(any(task.metadata.get("action_stage") == "ui_action" for task in plan_result.plan.tasks))
        self.assertTrue(
            any(
                item["action_stage"] == "ui_action" and item["status"] == "success"
                for item in execution.execution.outputs
            )
        )

    def test_browser_filter_workflow_executes_end_to_end(self) -> None:
        scene = self.ingestion.ingest_and_normalize_bytes(
            _fake_png(width=1366, height=768),
            source_id="scene:browser-filter",
            source_type="browser_capture",
        )
        layout = self.ocr.analyze_payload(
            scene,
            [
                {
                    "text": "Search Issues",
                    "left": 28,
                    "top": 22,
                    "width": 150,
                    "height": 20,
                    "confidence": 0.94,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Apply Filter",
                    "left": 490,
                    "top": 116,
                    "width": 110,
                    "height": 18,
                    "confidence": 0.92,
                    "line_id": "line-2",
                    "block_id": "block-b",
                },
            ],
            language_hint="en",
        )
        ui_state = self.grounding.build_state(
            scene,
            layout,
            candidates=[
                {
                    "role": "input",
                    "label": "Issue search",
                    "left": 30,
                    "top": 108,
                    "width": 430,
                    "height": 34,
                    "confidence": 0.89,
                    "selector_hints": ["#issue-search"],
                },
                {
                    "role": "button",
                    "label": "Apply Filter",
                    "left": 478,
                    "top": 108,
                    "width": 140,
                    "height": 34,
                    "confidence": 0.93,
                    "selector_hints": ["#apply-filter"],
                },
            ],
        )

        context = RunContext(
            run_id="run-browser-regression",
            goal="Apply filter",
            actor_id="boss",
        )
        plan_result = self.planner.plan(context, ui_state)
        execution = self.executor.execute(context, plan_result.plan, ui_state)

        self.assertEqual(execution.execution.status, "success")
        self.assertTrue(any(binding.action_stage == "ui_action" for binding in plan_result.bindings))
        self.assertTrue(
            any(
                item["action_stage"] == "ui_postcheck" and item["status"] == "success"
                for item in execution.execution.outputs
            )
        )

    def test_browser_checkout_workflow_preserves_summary_and_evidence_retrieval(self) -> None:
        scene = self.ingestion.ingest_and_normalize_bytes(
            _fake_png(width=1440, height=900),
            source_id="scene:browser-checkout",
            source_type="browser_capture",
        )
        layout = self.ocr.analyze_payload(
            scene,
            [
                {
                    "text": "Checkout",
                    "left": 34,
                    "top": 20,
                    "width": 94,
                    "height": 20,
                    "confidence": 0.95,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Total 42 dollars",
                    "left": 36,
                    "top": 58,
                    "width": 154,
                    "height": 18,
                    "confidence": 0.94,
                    "line_id": "line-2",
                    "block_id": "block-a",
                },
                {
                    "text": "Submit Order",
                    "left": 42,
                    "top": 106,
                    "width": 122,
                    "height": 18,
                    "confidence": 0.93,
                    "line_id": "line-3",
                    "block_id": "block-b",
                },
            ],
            language_hint="en",
        )
        ui_state = self.grounding.build_state(
            scene,
            layout,
            candidates=[
                {
                    "role": "button",
                    "label": "Submit Order",
                    "left": 34,
                    "top": 96,
                    "width": 180,
                    "height": 34,
                    "confidence": 0.94,
                    "selector_hints": ["#submit-order"],
                }
            ],
        )

        summary = self.extractor.summarize(scene, layout=layout, ui_state=ui_state)
        store_result = self.evidence_store.store_summary(summary)
        retrieval = self.retrieval.retrieve("submit order checkout total", namespace="multimodal")

        self.assertEqual(store_result.scene_id, scene.scene_id)
        self.assertGreaterEqual(len(store_result.citation_references), 2)
        self.assertGreaterEqual(retrieval.returned, 1)
        self.assertTrue(any(match.citation.source_type.startswith("multimodal_") for match in retrieval.matches))



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
