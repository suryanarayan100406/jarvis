"""Tests for P5-T8 user-correctable memory update workflow."""

from __future__ import annotations

import unittest

from runtime.memory import IngestedDocument, MemoryCorrectionError, MemoryCorrectionWorkflow, MemoryIndexingPipeline


def _doc(
    *,
    document_id: str,
    source_type: str,
    source_id: str,
    content: str,
    content_hash: str,
    metadata: dict,
) -> IngestedDocument:
    return IngestedDocument(
        document_id=document_id,
        source_type=source_type,
        source_id=source_id,
        content=content,
        content_hash=content_hash,
        metadata=metadata,
        ingested_at="2026-04-14T15:00:00Z",
    )


class MemoryCorrectionWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = MemoryIndexingPipeline()
        self.workflow = MemoryCorrectionWorkflow(self.index)

        self.index.index_document(
            _doc(
                document_id="d1",
                source_type="note",
                source_id="ops:1",
                content="Service health is stable with low latency",
                content_hash="h1",
                metadata={"topic": "health"},
            ),
            namespace="mem",
        )
        self.index.index_document(
            _doc(
                document_id="d2",
                source_type="log",
                source_id="sys:1",
                content="Deployment succeeded",
                content_hash="h2",
                metadata={"topic": "deploy"},
            ),
            namespace="mem",
        )

    def test_apply_correction_updates_indexed_record_and_versions(self) -> None:
        corrected = self.workflow.apply_correction(
            namespace="mem",
            source_type="note",
            source_id="ops:1",
            corrected_content="Service health is degraded with high latency",
            actor_id="boss",
            reason="observed regressions",
            metadata={"ticket": "INC-401"},
        )

        record = self.index.get_by_source(namespace="mem", source_type="note", source_id="ops:1")

        self.assertEqual(corrected.action, "updated")
        self.assertEqual(corrected.previous_version, 1)
        self.assertEqual(corrected.updated_version, 2)
        self.assertIn("degraded", record.current_content.lower())
        self.assertEqual(record.current_version, 2)
        self.assertEqual(record.current_metadata["corrected_by"], "boss")
        self.assertEqual(record.current_metadata["ticket"], "INC-401")

    def test_apply_correction_records_unchanged_when_no_delta(self) -> None:
        correction = self.workflow.apply_correction(
            namespace="mem",
            source_type="note",
            source_id="ops:1",
            corrected_content="Service health is stable with low latency",
            actor_id="boss",
            reason="confirm baseline",
        )

        record = self.index.get_by_source(namespace="mem", source_type="note", source_id="ops:1")

        self.assertEqual(correction.action, "unchanged")
        self.assertEqual(correction.previous_version, 1)
        self.assertEqual(correction.updated_version, 1)
        self.assertEqual(record.current_version, 1)

    def test_list_corrections_supports_filters(self) -> None:
        first = self.workflow.apply_correction(
            namespace="mem",
            source_type="note",
            source_id="ops:1",
            corrected_content="Service health indicates intermittent packet loss",
            actor_id="boss",
            reason="new evidence",
        )
        self.workflow.apply_correction(
            namespace="mem",
            source_type="log",
            source_id="sys:1",
            corrected_content="Deployment failed during rollout",
            actor_id="ops",
            reason="log correction",
        )

        by_actor = self.workflow.list_corrections(actor_id="boss")
        by_index = self.workflow.list_corrections(index_key=first.index_key)
        by_action = self.workflow.list_corrections(action="updated")

        self.assertEqual(len(by_actor), 1)
        self.assertEqual(by_actor[0].actor_id, "boss")
        self.assertEqual(len(by_index), 1)
        self.assertEqual(by_index[0].index_key, first.index_key)
        self.assertEqual(len(by_action), 2)

    def test_get_correction_returns_exact_record(self) -> None:
        correction = self.workflow.apply_correction(
            namespace="mem",
            source_type="note",
            source_id="ops:1",
            corrected_content="Service health changed",
            actor_id="boss",
            reason="manual fix",
        )

        loaded = self.workflow.get_correction(correction.correction_id)

        self.assertEqual(loaded.correction_id, correction.correction_id)
        self.assertEqual(loaded.reason, "manual fix")

    def test_apply_correction_raises_for_unknown_source(self) -> None:
        with self.assertRaises(MemoryCorrectionError):
            self.workflow.apply_correction(
                namespace="mem",
                source_type="note",
                source_id="missing",
                corrected_content="new content",
                actor_id="boss",
                reason="invalid source",
            )

    def test_apply_correction_validates_reason(self) -> None:
        with self.assertRaises(MemoryCorrectionError):
            self.workflow.apply_correction(
                namespace="mem",
                source_type="note",
                source_id="ops:1",
                corrected_content="new content",
                actor_id="boss",
                reason="   ",
            )


if __name__ == "__main__":
    unittest.main()
