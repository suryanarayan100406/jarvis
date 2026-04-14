"""Tests for P5-T4 memory retrieval engine with citation binding."""

from __future__ import annotations

import unittest

from runtime.memory import IngestedDocument, MemoryIndexingPipeline, MemoryRetrievalEngine


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
        ingested_at="2026-04-14T13:00:00Z",
    )


class MemoryRetrievalEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = MemoryIndexingPipeline()
        self.engine = MemoryRetrievalEngine(self.index)

        self.index.index_document(
            _doc(
                document_id="d1",
                source_type="note",
                source_id="ops:1",
                content="Deployment latency investigation shows db lock contention",
                content_hash="h1",
                metadata={"topic": "latency"},
            ),
            namespace="mem",
        )
        self.index.index_document(
            _doc(
                document_id="d2",
                source_type="log",
                source_id="sys:1",
                content="INFO api service healthy",
                content_hash="h2",
                metadata={"topic": "health"},
            ),
            namespace="mem",
        )
        self.index.index_document(
            _doc(
                document_id="d3",
                source_type="note",
                source_id="ops:duplicate",
                content="Deployment latency investigation shows db lock contention",
                content_hash="h1",
                metadata={"topic": "latency-dup"},
            ),
            namespace="mem",
        )

    def test_retrieves_ranked_matches_with_citations(self) -> None:
        result = self.engine.retrieve("deployment latency db", namespace="mem")

        self.assertGreaterEqual(result.total_candidates, 1)
        self.assertGreaterEqual(result.returned, 1)
        self.assertTrue(result.answer_context)
        self.assertEqual(len(result.matches), len(result.citations))
        self.assertIn("deployment latency", result.matches[0].excerpt.lower())

    def test_citations_bind_source_and_version(self) -> None:
        result = self.engine.retrieve("service healthy", namespace="mem")
        citation = result.citations[0]

        self.assertTrue(citation.citation_id.startswith("cite-"))
        self.assertTrue(citation.index_key)
        self.assertTrue(citation.source_type)
        self.assertTrue(citation.source_id)
        self.assertEqual(citation.version, 1)
        self.assertTrue(citation.content_hash)

    def test_limit_restricts_returned_matches(self) -> None:
        result = self.engine.retrieve("deployment service", namespace="mem", limit=1)

        self.assertEqual(result.returned, 1)
        self.assertEqual(len(result.matches), 1)

    def test_namespace_filtering(self) -> None:
        self.index.index_document(
            _doc(
                document_id="d4",
                source_type="note",
                source_id="other:1",
                content="outside namespace result",
                content_hash="h4",
                metadata={},
            ),
            namespace="other",
        )

        result = self.engine.retrieve("outside", namespace="mem")
        self.assertEqual(result.returned, 0)

    def test_excluding_duplicates_hides_duplicate_records(self) -> None:
        include_duplicates = self.engine.retrieve("deployment latency", namespace="mem", include_duplicates=True)
        exclude_duplicates = self.engine.retrieve("deployment latency", namespace="mem", include_duplicates=False)

        self.assertGreaterEqual(include_duplicates.returned, exclude_duplicates.returned)
        self.assertEqual(exclude_duplicates.returned, 1)

    def test_no_match_returns_empty_result(self) -> None:
        result = self.engine.retrieve("nonexistent query term", namespace="mem")

        self.assertEqual(result.total_candidates, 0)
        self.assertEqual(result.returned, 0)
        self.assertEqual(result.answer_context, "")


if __name__ == "__main__":
    unittest.main()
