"""Tests for P8-T7 document and image summary extraction with citations."""

from __future__ import annotations

import unittest

from runtime.multimodal import (
    DocumentImageSummaryExtractor,
    MultimodalSummaryError,
    OCRLayoutAnalyzer,
    ScreenshotIngestionPipeline,
    UIGroundingModel,
)


class DocumentImageSummaryExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ingestion = ScreenshotIngestionPipeline()
        self.ocr = OCRLayoutAnalyzer(min_confidence=0.35)
        self.grounding = UIGroundingModel(min_element_confidence=0.45)
        self.extractor = DocumentImageSummaryExtractor()

    def test_text_rich_document_summary_emits_ocr_citations(self) -> None:
        scene = self._scene(source_id="scene:doc")
        layout = self.ocr.analyze_payload(
            scene,
            [
                {
                    "text": "Quarterly Platform Review",
                    "left": 30,
                    "top": 28,
                    "width": 260,
                    "height": 20,
                    "confidence": 0.95,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Revenue increased by 14 percent",
                    "left": 30,
                    "top": 58,
                    "width": 280,
                    "height": 18,
                    "confidence": 0.92,
                    "line_id": "line-2",
                    "block_id": "block-a",
                },
            ],
            language_hint="en",
        )

        result = self.extractor.summarize(scene, layout=layout)

        citation_types = {citation.source_type for citation in result.citations}
        self.assertIn("ocr_line", citation_types)
        self.assertIn("ocr_block", citation_types)
        self.assertIn("scene_metadata", citation_types)
        self.assertIn("[1]", result.summary_text)
        self.assertEqual(result.language_hint, "en")

    def test_ui_summary_adds_actionable_element_citations(self) -> None:
        scene = self._scene(source_id="scene:ui")
        layout = self.ocr.analyze_payload(
            scene,
            [
                {
                    "text": "Deploy",
                    "left": 16,
                    "top": 24,
                    "width": 60,
                    "height": 18,
                    "confidence": 0.9,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "Now",
                    "left": 80,
                    "top": 24,
                    "width": 36,
                    "height": 18,
                    "confidence": 0.9,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
            ],
        )
        ui_state = self.grounding.build_state(
            scene,
            layout,
            candidates=[
                {
                    "role": "button",
                    "left": 10,
                    "top": 18,
                    "width": 120,
                    "height": 30,
                    "confidence": 0.93,
                    "selector_hints": ["#deploy"],
                }
            ],
        )

        result = self.extractor.summarize(scene, layout=layout, ui_state=ui_state)

        self.assertTrue(any(citation.source_type == "ui_element" for citation in result.citations))
        self.assertTrue(any("actionable" in point.lower() for point in result.key_points))

    def test_metadata_fallback_is_used_when_no_text_or_ui(self) -> None:
        scene = self._scene(source_id="scene:image")
        empty_layout = self.ocr.analyze_payload(scene, [])

        result = self.extractor.summarize(scene, layout=empty_layout)

        self.assertTrue(any("metadata-only" in warning.lower() for warning in result.warnings))
        self.assertEqual(len(result.citations), 1)
        self.assertEqual(result.citations[0].source_type, "scene_metadata")
        self.assertIn("No OCR text", result.summary_text)

    def test_low_confidence_content_emits_confidence_warning(self) -> None:
        scene = self._scene(source_id="scene:low-confidence")
        layout = self.ocr.analyze_payload(
            scene,
            [
                {
                    "text": "token",
                    "left": 20,
                    "top": 20,
                    "width": 40,
                    "height": 16,
                    "confidence": 0.2,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": "extract",
                    "left": 64,
                    "top": 20,
                    "width": 56,
                    "height": 16,
                    "confidence": 0.22,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
            ],
        )

        result = self.extractor.summarize(scene, layout=layout)

        self.assertTrue(any("confidence" in warning.lower() for warning in result.warnings))
        self.assertLess(result.overall_confidence, 0.35)

    def test_same_inputs_generate_deterministic_summary(self) -> None:
        scene = self._scene(source_id="scene:deterministic")
        layout = self.ocr.analyze_payload(
            scene,
            [
                {
                    "text": "Operations Log",
                    "left": 20,
                    "top": 20,
                    "width": 140,
                    "height": 18,
                    "confidence": 0.95,
                    "line_id": "line-1",
                    "block_id": "block-a",
                }
            ],
        )

        result_a = self.extractor.summarize(scene, layout=layout)
        result_b = self.extractor.summarize(scene, layout=layout)

        self.assertEqual(result_a.summary_id, result_b.summary_id)
        self.assertEqual(result_a.summary_text, result_b.summary_text)
        self.assertEqual(
            tuple(citation.citation_id for citation in result_a.citations),
            tuple(citation.citation_id for citation in result_b.citations),
        )

    def test_scene_mismatch_raises_error(self) -> None:
        scene_a = self._scene(source_id="scene:a")
        scene_b = self._scene(source_id="scene:b")
        layout = self.ocr.analyze_payload(
            scene_a,
            [
                {
                    "text": "Mismatch",
                    "left": 20,
                    "top": 20,
                    "width": 70,
                    "height": 18,
                    "confidence": 0.9,
                }
            ],
        )

        with self.assertRaises(MultimodalSummaryError):
            self.extractor.summarize(scene_b, layout=layout)

    def _scene(self, *, source_id: str):
        return self.ingestion.ingest_and_normalize_bytes(
            _fake_png(width=1920, height=1080),
            source_id=source_id,
            source_type="desktop_capture",
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
