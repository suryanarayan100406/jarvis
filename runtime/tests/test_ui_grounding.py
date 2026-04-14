"""Tests for P8-T3 UI element grounding and state representation."""

from __future__ import annotations

import unittest

from runtime.multimodal import (
    OCRLayoutAnalyzer,
    ScreenshotIngestionPipeline,
    UIGroundingError,
    UIGroundingModel,
)


class UIGroundingModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ingestion = ScreenshotIngestionPipeline()
        self.scene = self.ingestion.ingest_and_normalize_bytes(
            _fake_png(width=1920, height=1080),
            source_id="scene:ui",
            source_type="desktop_capture",
        )
        self.ocr = OCRLayoutAnalyzer(min_confidence=0.35)
        self.model = UIGroundingModel(min_element_confidence=0.45)

    def test_build_state_merges_detector_with_ocr_text_and_confidence(self) -> None:
        layout = self.ocr.analyze_payload(
            self.scene,
            [
                {
                    "text": "Deploy",
                    "left": 20,
                    "top": 30,
                    "width": 60,
                    "height": 18,
                    "confidence": 0.91,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Now",
                    "left": 86,
                    "top": 30,
                    "width": 40,
                    "height": 18,
                    "confidence": 0.9,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
            ],
        )

        state = self.model.build_state(
            self.scene,
            layout,
            candidates=[
                {
                    "role": "button",
                    "left": 16,
                    "top": 24,
                    "width": 128,
                    "height": 30,
                    "confidence": 0.95,
                    "selector_hints": ["#deploy-button"],
                }
            ],
        )

        self.assertEqual(len(state.elements), 1)
        element = state.elements[0]
        self.assertEqual(element.role, "button")
        self.assertEqual(element.label, "Deploy Now")
        self.assertGreaterEqual(element.confidence, 0.92)
        self.assertEqual(element.source_signals, ("detector", "ocr"))
        self.assertIn("#deploy-button", element.selector_hints)
        self.assertIn(element.element_id, state.actionable_element_ids)
        self.assertEqual(state.reading_order, (element.element_id,))

    def test_build_state_falls_back_to_ocr_elements_when_no_candidates(self) -> None:
        layout = self.ocr.analyze_payload(
            self.scene,
            [
                {
                    "text": "Settings",
                    "left": 100,
                    "top": 80,
                    "width": 75,
                    "height": 18,
                    "confidence": 0.88,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
        )

        state = self.model.build_state(self.scene, layout)

        self.assertEqual(len(state.elements), 1)
        element = state.elements[0]
        self.assertEqual(element.label, "Settings")
        self.assertEqual(element.source_signals, ("ocr",))
        self.assertEqual(element.text_line_ids, ("line-1",))
        self.assertIn(element.element_id, state.actionable_element_ids)
        self.assertEqual(state.warnings, ())

    def test_low_confidence_actionable_elements_are_gated(self) -> None:
        strict_model = UIGroundingModel(min_element_confidence=0.60)
        layout = self.ocr.analyze_payload(
            self.scene,
            [
                {
                    "text": "Delete",
                    "left": 15,
                    "top": 120,
                    "width": 60,
                    "height": 18,
                    "confidence": 0.2,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
        )

        state = strict_model.build_state(
            self.scene,
            layout,
            candidates=[
                {
                    "role": "button",
                    "left": 10,
                    "top": 116,
                    "width": 84,
                    "height": 30,
                    "confidence": 0.25,
                }
            ],
        )

        self.assertEqual(len(state.low_confidence_element_ids), 1)
        self.assertEqual(state.actionable_element_ids, ())
        self.assertTrue(any("threshold" in warning.lower() for warning in state.warnings))

    def test_duplicate_candidates_keep_highest_confidence_element(self) -> None:
        layout = self.ocr.analyze_payload(
            self.scene,
            [
                {
                    "text": "Save",
                    "left": 200,
                    "top": 180,
                    "width": 45,
                    "height": 18,
                    "confidence": 0.9,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
        )

        state = self.model.build_state(
            self.scene,
            layout,
            candidates=[
                {
                    "role": "button",
                    "label": "Save",
                    "left": 194,
                    "top": 174,
                    "width": 70,
                    "height": 28,
                    "confidence": 0.92,
                },
                {
                    "role": "button",
                    "label": "Save",
                    "left": 194,
                    "top": 174,
                    "width": 70,
                    "height": 28,
                    "confidence": 0.40,
                },
            ],
        )

        self.assertEqual(len(state.elements), 1)
        self.assertGreaterEqual(state.elements[0].confidence, 0.90)

    def test_scene_layout_mismatch_raises_error(self) -> None:
        layout = self.ocr.analyze_payload(
            self.scene,
            [
                {
                    "text": "OK",
                    "left": 20,
                    "top": 20,
                    "width": 30,
                    "height": 16,
                    "confidence": 0.95,
                }
            ],
        )
        different_scene = self.ingestion.ingest_and_normalize_bytes(
            _fake_png(width=800, height=600),
            source_id="scene:other",
            source_type="desktop_capture",
        )

        with self.assertRaises(UIGroundingError):
            self.model.build_state(different_scene, layout)

    def test_invalid_candidate_values_raise_errors(self) -> None:
        with self.assertRaises(UIGroundingError):
            self.model.normalize_candidates(
                [
                    {
                        "left": -1,
                        "top": 0,
                        "width": 10,
                        "height": 10,
                        "confidence": 0.9,
                    }
                ]
            )

        with self.assertRaises(UIGroundingError):
            self.model.normalize_candidates(
                [
                    {
                        "left": 0,
                        "top": 0,
                        "width": 10,
                        "height": 10,
                        "confidence": 1.5,
                    }
                ]
            )


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
