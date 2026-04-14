"""Tests for P8-T1 screenshot ingestion and normalization pipeline."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.multimodal import ScreenshotIngestionError, ScreenshotIngestionPipeline


class ScreenshotIngestionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = ScreenshotIngestionPipeline(max_bytes=1024 * 1024, target_max_dimension=1920)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ingest_png_bytes_extracts_dimensions_and_metadata(self) -> None:
        raw = _fake_png(width=1920, height=1080)

        capture = self.pipeline.ingest_bytes(
            raw,
            source_id="screen:primary",
            source_type="desktop_capture",
            metadata={"window": "main"},
        )

        self.assertEqual(capture.image_format, "png")
        self.assertEqual(capture.width, 1920)
        self.assertEqual(capture.height, 1080)
        self.assertEqual(capture.source_id, "screen:primary")
        self.assertEqual(capture.source_type, "desktop_capture")
        self.assertEqual(capture.byte_size, len(raw))
        self.assertEqual(capture.metadata["window"], "main")
        self.assertEqual(len(capture.content_hash), 64)

    def test_ingest_jpeg_bytes_extracts_dimensions(self) -> None:
        raw = _fake_jpeg(width=32, height=16)

        capture = self.pipeline.ingest_bytes(
            raw,
            source_id="screen:jpeg",
        )

        self.assertEqual(capture.image_format, "jpeg")
        self.assertEqual(capture.width, 32)
        self.assertEqual(capture.height, 16)

    def test_ingest_file_adds_path_metadata(self) -> None:
        image_path = Path(self.temp_dir.name) / "shot.png"
        image_path.write_bytes(_fake_png(width=800, height=600))

        capture = self.pipeline.ingest_file(image_path, source_type="desktop_file")

        self.assertEqual(capture.image_format, "png")
        self.assertEqual(capture.source_type, "desktop_file")
        self.assertEqual(capture.metadata["path"], str(image_path))
        self.assertEqual(capture.metadata["file_name"], "shot.png")

    def test_normalize_capture_downscales_to_target_dimension(self) -> None:
        capture = self.pipeline.ingest_bytes(
            _fake_png(width=3840, height=2160),
            source_id="screen:4k",
        )

        scene = self.pipeline.normalize_capture(capture, target_max_dimension=1920)

        self.assertEqual(scene.original_width, 3840)
        self.assertEqual(scene.original_height, 2160)
        self.assertEqual(scene.normalized_width, 1920)
        self.assertEqual(scene.normalized_height, 1080)
        self.assertEqual(scene.orientation, "landscape")
        self.assertEqual(scene.scale_ratio, 0.5)
        self.assertEqual(scene.metadata["aspect_bucket"], "widescreen")

    def test_ingest_batch_deduplicates_content_hashes(self) -> None:
        image_a = _fake_png(width=100, height=100)
        image_b = _fake_png(width=100, height=100)
        image_c = _fake_png(width=200, height=100)

        summary = self.pipeline.ingest_batch([image_a, image_b, image_c], source_type="batch_capture")

        self.assertEqual(summary.total_items, 3)
        self.assertEqual(summary.ingested_items, 2)
        self.assertEqual(summary.duplicate_items, 1)
        self.assertEqual(len(summary.captures), 2)

    def test_invalid_inputs_raise_errors(self) -> None:
        with self.assertRaises(ScreenshotIngestionError):
            self.pipeline.ingest_bytes(b"", source_id="screen:empty")

        with self.assertRaises(ScreenshotIngestionError):
            self.pipeline.ingest_bytes(b"not-an-image", source_id="screen:bad")

        capture = self.pipeline.ingest_bytes(_fake_png(width=10, height=10), source_id="screen:small")
        with self.assertRaises(ScreenshotIngestionError):
            self.pipeline.normalize_capture(capture, target_max_dimension=0)


def _fake_png(*, width: int, height: int) -> bytes:
    if width < 1 or height < 1:
        raise ValueError("width and height must be positive")
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def _fake_jpeg(*, width: int, height: int) -> bytes:
    if width < 1 or height < 1:
        raise ValueError("width and height must be positive")

    sof_payload = (
        b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    segment_length = len(sof_payload) + 2

    return (
        b"\xff\xd8"
        + b"\xff\xc0"
        + segment_length.to_bytes(2, "big")
        + sof_payload
        + b"\xff\xd9"
    )


if __name__ == "__main__":
    unittest.main()
