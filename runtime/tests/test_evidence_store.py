"""Tests for P8-T8 multimodal evidence store integrated with memory indexing."""

from __future__ import annotations

import unittest

from runtime.memory import MemoryIndexingPipeline, MemoryRetrievalEngine
from runtime.multimodal import (
    DocumentImageSummaryExtractor,
    MultimodalEvidenceStore,
    MultimodalEvidenceStoreError,
    OCRLayoutAnalyzer,
    ScreenshotIngestionPipeline,
)


class MultimodalEvidenceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = MemoryIndexingPipeline()
        self.store = MultimodalEvidenceStore(self.index, default_namespace="multimodal")
        self.retrieval = MemoryRetrievalEngine(self.index)
        self.ingestion = ScreenshotIngestionPipeline()
        self.ocr = OCRLayoutAnalyzer(min_confidence=0.35)
        self.extractor = DocumentImageSummaryExtractor()

    def test_store_summary_indexes_summary_citations_and_bundle(self) -> None:
        summary = self._summary(source_id="scene:evidence", title="Quarterly Platform Review", body="Revenue up by 14 percent")

        result = self.store.store_summary(summary)
        records = self.index.list_records(namespace="multimodal")
        source_types = {record.source_type for record in records}

        self.assertEqual(result.scene_id, summary.scene_id)
        self.assertEqual(result.summary_reference.source_type, "multimodal_summary")
        self.assertEqual(result.bundle_reference.source_type, "multimodal_evidence_bundle")
        self.assertEqual(len(result.citation_references), len(summary.citations))
        self.assertIn("multimodal_summary", source_types)
        self.assertIn("multimodal_citation", source_types)
        self.assertIn("multimodal_evidence_bundle", source_types)

    def test_storing_same_summary_is_idempotent(self) -> None:
        summary = self._summary(source_id="scene:idempotent", title="Ops Snapshot", body="All services healthy")

        first = self.store.store_summary(summary)
        second = self.store.store_summary(summary)

        self.assertEqual(first.summary_reference.action, "created")
        self.assertEqual(second.summary_reference.action, "unchanged")
        self.assertEqual(second.bundle_reference.action, "unchanged")
        self.assertTrue(all(ref.action == "unchanged" for ref in second.citation_references))
        self.assertEqual(len(self.index.list_versions(first.summary_reference.index_key)), 1)

    def test_load_scene_evidence_returns_records_for_scene(self) -> None:
        summary = self._summary(source_id="scene:load", title="Incident Review", body="Root cause isolated")
        self.store.store_summary(summary)

        bundle = self.store.load_scene_evidence(summary.scene_id)

        self.assertIsNotNone(bundle.summary_record)
        self.assertIsNotNone(bundle.bundle_record)
        self.assertEqual(len(bundle.citation_records), len(summary.citations))
        self.assertEqual(bundle.scene_id, summary.scene_id)

    def test_stored_evidence_is_queryable_via_memory_retrieval(self) -> None:
        summary = self._summary(source_id="scene:query", title="Database Maintenance", body="Indexes rebuilt overnight")
        self.store.store_summary(summary)

        result = self.retrieval.retrieve("database indexes rebuilt", namespace="multimodal")

        self.assertGreaterEqual(result.returned, 1)
        self.assertTrue(any(match.citation.source_type.startswith("multimodal_") for match in result.matches))

    def test_invalid_summary_input_raises_error(self) -> None:
        with self.assertRaises(MultimodalEvidenceStoreError):
            self.store.store_summary("invalid-summary")  # type: ignore[arg-type]

    def _summary(self, *, source_id: str, title: str, body: str):
        scene = self.ingestion.ingest_and_normalize_bytes(
            _fake_png(width=1920, height=1080),
            source_id=source_id,
            source_type="desktop_capture",
        )
        layout = self.ocr.analyze_payload(
            scene,
            [
                {
                    "text": title,
                    "left": 24,
                    "top": 30,
                    "width": max(80, len(title) * 7),
                    "height": 20,
                    "confidence": 0.95,
                    "line_id": "line-1",
                    "block_id": "block-a",
                },
                {
                    "text": body,
                    "left": 24,
                    "top": 58,
                    "width": max(120, len(body) * 7),
                    "height": 18,
                    "confidence": 0.92,
                    "line_id": "line-2",
                    "block_id": "block-a",
                },
            ],
            language_hint="en",
        )
        return self.extractor.summarize(scene, layout=layout)


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
