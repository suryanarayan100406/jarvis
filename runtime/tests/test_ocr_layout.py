"""Tests for P8-T2 OCR and layout analysis."""

from __future__ import annotations

import unittest

from runtime.multimodal import OCRLayoutAnalyzer, OCRLayoutError, ScreenshotIngestionPipeline


class OCRLayoutAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ingestion = ScreenshotIngestionPipeline()
        self.scene = self.ingestion.ingest_and_normalize_bytes(
            _fake_png(width=1920, height=1080),
            source_id="scene:1",
            source_type="desktop_capture",
        )
        self.analyzer = OCRLayoutAnalyzer(min_confidence=0.35)

    def test_parse_ocr_payload_normalizes_spans(self) -> None:
        payload = [
            {"text": "  Server  ", "left": 10, "top": 12, "width": 70, "height": 18, "confidence": 0.95},
            {"text": "Health", "left": 90, "top": 12, "width": 55, "height": 18, "confidence": 0.92},
            {"text": "", "left": 0, "top": 0, "width": 1, "height": 1, "confidence": 1.0},
        ]

        spans = self.analyzer.parse_ocr_payload(self.scene, payload)

        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0].text, "Server")
        self.assertEqual(spans[1].text, "Health")
        self.assertEqual(spans[0].left, 10)
        self.assertEqual(spans[0].top, 12)

    def test_analyze_payload_builds_lines_blocks_and_reading_order(self) -> None:
        payload = [
            {
                "text": "CPU",
                "left": 10,
                "top": 20,
                "width": 40,
                "height": 15,
                "confidence": 0.9,
                "line_id": "line-1",
                "block_id": "block-a",
            },
            {
                "text": "Usage",
                "left": 60,
                "top": 20,
                "width": 50,
                "height": 15,
                "confidence": 0.88,
                "line_id": "line-1",
                "block_id": "block-a",
            },
            {
                "text": "Memory",
                "left": 10,
                "top": 45,
                "width": 65,
                "height": 15,
                "confidence": 0.9,
                "line_id": "line-2",
                "block_id": "block-a",
            },
            {
                "text": "Disk",
                "left": 400,
                "top": 20,
                "width": 40,
                "height": 15,
                "confidence": 0.93,
                "line_id": "line-3",
                "block_id": "block-b",
            },
        ]

        result = self.analyzer.analyze_payload(self.scene, payload, language_hint="en")

        self.assertEqual(result.language_hint, "en")
        self.assertEqual(len(result.spans), 4)
        self.assertEqual(len(result.lines), 3)
        self.assertEqual(len(result.blocks), 2)
        self.assertEqual(result.reading_order, ("line-1", "line-3", "line-2"))
        self.assertIn("CPU Usage", result.full_text)
        self.assertGreater(result.avg_confidence, 0.85)
        self.assertEqual(result.warnings, ())

    def test_analyze_emits_warnings_for_low_confidence(self) -> None:
        payload = [
            {
                "text": "token",
                "left": 10,
                "top": 10,
                "width": 20,
                "height": 15,
                "confidence": 0.2,
                "line_id": "line-1",
                "block_id": "block-a",
            },
            {
                "text": "extract",
                "left": 35,
                "top": 10,
                "width": 35,
                "height": 15,
                "confidence": 0.25,
                "line_id": "line-1",
                "block_id": "block-a",
            },
            {
                "text": "now",
                "left": 10,
                "top": 30,
                "width": 18,
                "height": 15,
                "confidence": 0.3,
                "line_id": "line-2",
                "block_id": "block-a",
            },
        ]

        result = self.analyzer.analyze_payload(self.scene, payload)

        self.assertGreaterEqual(result.low_confidence_ratio, 1.0)
        self.assertGreaterEqual(len(result.warnings), 1)
        self.assertTrue(any("confidence" in warning.lower() for warning in result.warnings))

    def test_empty_payload_returns_empty_layout_with_warning(self) -> None:
        result = self.analyzer.analyze_payload(self.scene, [])

        self.assertEqual(result.full_text, "")
        self.assertEqual(result.spans, ())
        self.assertEqual(result.lines, ())
        self.assertEqual(result.blocks, ())
        self.assertEqual(result.reading_order, ())
        self.assertEqual(len(result.warnings), 1)

    def test_invalid_payload_values_raise_errors(self) -> None:
        with self.assertRaises(OCRLayoutError):
            self.analyzer.parse_ocr_payload(self.scene, [{"text": "bad", "left": -1, "top": 0, "width": 1, "height": 1}])

        with self.assertRaises(OCRLayoutError):
            self.analyzer.parse_ocr_payload(self.scene, [{"text": "bad", "left": 0, "top": 0, "width": 0, "height": 1}])

        with self.assertRaises(OCRLayoutError):
            self.analyzer.parse_ocr_payload(
                self.scene,
                [{"text": "bad", "left": 0, "top": 0, "width": 1, "height": 1, "confidence": 1.5}],
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
