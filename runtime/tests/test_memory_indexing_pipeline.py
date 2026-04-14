"""Tests for P5-T3 memory indexing pipeline."""

from __future__ import annotations

import unittest

from runtime.memory import IngestedDocument, MemoryIndexingPipeline


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
        ingested_at="2026-04-14T12:00:00Z",
    )


class MemoryIndexingPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = MemoryIndexingPipeline()

    def test_create_record_on_first_index(self) -> None:
        result = self.pipeline.index_document(
            _doc(
                document_id="doc-1",
                source_type="note",
                source_id="ops:1",
                content="Investigate api latency",
                content_hash="hash-1",
                metadata={"tag": "ops"},
            ),
            namespace="memory",
        )

        record = self.pipeline.get_record(result.index_key)

        self.assertEqual(result.action, "created")
        self.assertEqual(result.version, 1)
        self.assertEqual(record.current_version, 1)
        self.assertIsNone(record.duplicate_of)

    def test_unchanged_content_and_metadata_does_not_increment_version(self) -> None:
        document = _doc(
            document_id="doc-1",
            source_type="note",
            source_id="ops:1",
            content="Investigate api latency",
            content_hash="hash-1",
            metadata={"tag": "ops"},
        )
        first = self.pipeline.index_document(document, namespace="memory")
        second = self.pipeline.index_document(document, namespace="memory")

        self.assertEqual(first.version, 1)
        self.assertEqual(second.action, "unchanged")
        self.assertEqual(second.version, 1)
        self.assertEqual(len(self.pipeline.list_versions(first.index_key)), 1)

    def test_changed_content_increments_version(self) -> None:
        first = self.pipeline.index_document(
            _doc(
                document_id="doc-1",
                source_type="log",
                source_id="sys:1",
                content="INFO service started",
                content_hash="hash-1",
                metadata={"line": 1},
            ),
            namespace="memory",
        )
        second = self.pipeline.index_document(
            _doc(
                document_id="doc-2",
                source_type="log",
                source_id="sys:1",
                content="WARN service delayed",
                content_hash="hash-2",
                metadata={"line": 1},
            ),
            namespace="memory",
        )

        versions = self.pipeline.list_versions(first.index_key)

        self.assertEqual(second.action, "updated")
        self.assertEqual(second.version, 2)
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[1].content_hash, "hash-2")

    def test_deduplicates_by_content_hash_across_sources(self) -> None:
        primary = self.pipeline.index_document(
            _doc(
                document_id="doc-a",
                source_type="file",
                source_id="docs/a.md",
                content="Same payload",
                content_hash="dup-hash",
                metadata={"path": "a"},
            ),
            namespace="memory",
        )
        duplicate = self.pipeline.index_document(
            _doc(
                document_id="doc-b",
                source_type="note",
                source_id="ops:99",
                content="Same payload",
                content_hash="dup-hash",
                metadata={"path": "b"},
            ),
            namespace="memory",
        )

        primary_record = self.pipeline.get_record(primary.index_key)
        duplicate_record = self.pipeline.get_record(duplicate.index_key)

        self.assertIsNone(primary_record.duplicate_of)
        self.assertEqual(duplicate.action, "deduplicated")
        self.assertEqual(duplicate_record.duplicate_of, primary.index_key)

    def test_batch_indexing_summary_counts(self) -> None:
        docs = [
            _doc(
                document_id="doc-1",
                source_type="note",
                source_id="ops:1",
                content="alpha",
                content_hash="h-alpha",
                metadata={"i": 1},
            ),
            _doc(
                document_id="doc-2",
                source_type="note",
                source_id="ops:2",
                content="alpha",
                content_hash="h-alpha",
                metadata={"i": 2},
            ),
            _doc(
                document_id="doc-3",
                source_type="note",
                source_id="ops:1",
                content="alpha",
                content_hash="h-alpha",
                metadata={"i": 1},
            ),
        ]

        summary = self.pipeline.index_documents(docs, namespace="memory")

        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.created, 1)
        self.assertEqual(summary.deduplicated, 1)
        self.assertEqual(summary.unchanged, 1)

    def test_list_records_can_exclude_duplicates(self) -> None:
        self.pipeline.index_document(
            _doc(
                document_id="doc-a",
                source_type="file",
                source_id="docs/a.md",
                content="same",
                content_hash="dup-hash",
                metadata={},
            ),
            namespace="memory",
        )
        self.pipeline.index_document(
            _doc(
                document_id="doc-b",
                source_type="note",
                source_id="ops:2",
                content="same",
                content_hash="dup-hash",
                metadata={},
            ),
            namespace="memory",
        )

        all_records = self.pipeline.list_records(namespace="memory", include_duplicates=True)
        canonical_records = self.pipeline.list_records(namespace="memory", include_duplicates=False)

        self.assertEqual(len(all_records), 2)
        self.assertEqual(len(canonical_records), 1)


if __name__ == "__main__":
    unittest.main()
