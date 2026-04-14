"""Tests for P8-T9 low-confidence fallback strategy classification."""

from __future__ import annotations

import unittest

from runtime.multimodal import (
    OCRLayoutAnalyzer,
    ScreenshotIngestionPipeline,
    UIGroundingModel,
    UIStateRepresentation,
    VisualConfidenceFallbackStrategy,
)


class VisualConfidenceFallbackStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ingestion = ScreenshotIngestionPipeline()
        self.ocr = OCRLayoutAnalyzer(min_confidence=0.35)
        self.grounding = UIGroundingModel(min_element_confidence=0.45)
        self.strategy = VisualConfidenceFallbackStrategy(
            min_autonomous_confidence=0.5,
            min_confirmation_confidence=0.3,
            max_low_confidence_ratio=0.65,
        )

    def test_confident_target_proceeds_without_fallback(self) -> None:
        state = self._build_ui_state(
            [
                {
                    "text": "Overview",
                    "left": 20,
                    "top": 30,
                    "width": 80,
                    "height": 18,
                    "confidence": 0.94,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
            candidates=[
                {
                    "role": "button",
                    "label": "Open",
                    "left": 700,
                    "top": 580,
                    "width": 120,
                    "height": 36,
                    "confidence": 0.88,
                }
            ],
        )
        target = _highest_confidence_actionable(state)

        decision = self.strategy.assess(state, target)

        self.assertEqual(decision.mode, "proceed")
        self.assertEqual(decision.reason, "confidence_within_autonomous_limits")

    def test_borderline_target_requires_confirmation_mode(self) -> None:
        state = self._build_ui_state(
            [
                {
                    "text": "Telemetry",
                    "left": 20,
                    "top": 30,
                    "width": 80,
                    "height": 18,
                    "confidence": 0.9,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
            candidates=[
                {
                    "role": "button",
                    "label": "Proceed",
                    "left": 700,
                    "top": 580,
                    "width": 120,
                    "height": 36,
                    "confidence": 0.42,
                }
            ],
        )
        target = _highest_confidence_actionable(state)

        decision = self.strategy.assess(state, target)

        self.assertEqual(decision.mode, "confirm")
        self.assertEqual(decision.reason, "element_confidence_below_autonomous_threshold")
        self.assertIn("require_confirmation_checkpoint", decision.recommended_actions)

    def test_critical_target_confidence_defers_autonomous_action(self) -> None:
        state = self._build_ui_state(
            [
                {
                    "text": "Logs",
                    "left": 20,
                    "top": 30,
                    "width": 48,
                    "height": 18,
                    "confidence": 0.9,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
            candidates=[
                {
                    "role": "button",
                    "label": "Proceed",
                    "left": 700,
                    "top": 580,
                    "width": 120,
                    "height": 36,
                    "confidence": 0.12,
                }
            ],
        )
        target = _highest_confidence_actionable(state)

        decision = self.strategy.assess(state, target)

        self.assertEqual(decision.mode, "defer")
        self.assertEqual(decision.reason, "element_confidence_critical")
        self.assertIn("escalate_to_operator", decision.recommended_actions)

    def test_scene_wide_low_confidence_requests_confirmation(self) -> None:
        state = self._build_ui_state(
            [
                {
                    "text": "panel one",
                    "left": 20,
                    "top": 30,
                    "width": 90,
                    "height": 18,
                    "confidence": 0.36,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "panel two",
                    "left": 20,
                    "top": 60,
                    "width": 90,
                    "height": 18,
                    "confidence": 0.37,
                    "line_id": "line-2",
                    "block_id": "block-b",
                },
                {
                    "text": "panel three",
                    "left": 20,
                    "top": 90,
                    "width": 90,
                    "height": 18,
                    "confidence": 0.38,
                    "line_id": "line-3",
                    "block_id": "block-c",
                },
                {
                    "text": "panel four",
                    "left": 20,
                    "top": 120,
                    "width": 90,
                    "height": 18,
                    "confidence": 0.39,
                    "line_id": "line-4",
                    "block_id": "block-d",
                },
                {
                    "text": "panel five",
                    "left": 20,
                    "top": 150,
                    "width": 90,
                    "height": 18,
                    "confidence": 0.4,
                    "line_id": "line-5",
                    "block_id": "block-e",
                },
            ],
            candidates=[
                {
                    "role": "button",
                    "label": "Open",
                    "left": 700,
                    "top": 580,
                    "width": 120,
                    "height": 36,
                    "confidence": 0.86,
                }
            ],
        )
        target = _highest_confidence_actionable(state)

        decision = self.strategy.assess(state, target)

        self.assertEqual(decision.mode, "confirm")
        self.assertEqual(decision.reason, "scene_low_confidence_ratio_high")
        self.assertGreaterEqual(decision.low_confidence_ratio, 0.65)

    def _build_ui_state(
        self,
        ocr_payload: list[dict[str, object]],
        *,
        candidates: list[dict[str, object]],
    ) -> UIStateRepresentation:
        scene = self.ingestion.ingest_and_normalize_bytes(
            _fake_png(width=1920, height=1080),
            source_id="scene:fallback-tests",
            source_type="desktop_capture",
        )
        layout = self.ocr.analyze_payload(scene, ocr_payload)
        return self.grounding.build_state(scene, layout, candidates=candidates)


def _highest_confidence_actionable(state: UIStateRepresentation):
    actionable = [element for element in state.elements if element.actionable]
    return max(actionable, key=lambda element: element.confidence)


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
