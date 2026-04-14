"""Quality tests for P5-T11 retrieval relevance and citation fidelity."""

from __future__ import annotations

import unittest

from runtime.memory import IngestedDocument, MemoryIndexingPipeline, MemoryPrivacyFilter, MemoryRetrievalEngine


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
        ingested_at="2026-04-14T18:00:00Z",
    )


class MemoryRetrievalQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = MemoryIndexingPipeline()
        self.engine = MemoryRetrievalEngine(self.index)

        self.index.index_document(
            _doc(
                document_id="db-primary",
                source_type="note",
                source_id="db:primary",
                content="Database latency incident caused by lock contention in orders table",
                content_hash="h-db-primary",
                metadata={"topic": "database"},
            ),
            namespace="mem",
        )
        self.index.index_document(
            _doc(
                document_id="deploy-primary",
                source_type="note",
                source_id="deploy:primary",
                content="Deployment rollback completed after alert and health checks",
                content_hash="h-deploy-primary",
                metadata={"topic": "deploy"},
            ),
            namespace="mem",
        )
        self.index.index_document(
            _doc(
                document_id="ui-primary",
                source_type="file",
                source_id="ui:primary",
                content="Frontend palette update improves dashboard readability and contrast",
                content_hash="h-ui-primary",
                metadata={"topic": "frontend"},
            ),
            namespace="mem",
        )

    def test_benchmark_queries_return_expected_topical_match(self) -> None:
        benchmark = (
            ("database latency incident", "mem:note:db:primary"),
            ("deployment rollback alert", "mem:note:deploy:primary"),
            ("frontend palette dashboard", "mem:file:ui:primary"),
        )

        for query, expected_index_key in benchmark:
            with self.subTest(query=query):
                result = self.engine.retrieve(query, namespace="mem", limit=3)
                self.assertGreater(result.returned, 0)
                self.assertEqual(result.matches[0].index_key, expected_index_key)

    def test_citation_tracks_latest_record_version_and_hash(self) -> None:
        self.index.index_document(
            _doc(
                document_id="db-primary-v2",
                source_type="note",
                source_id="db:primary",
                content="Database latency incident mitigated by lock timeout tuning",
                content_hash="h-db-primary-v2",
                metadata={"topic": "database", "revision": 2},
            ),
            namespace="mem",
        )

        result = self.engine.retrieve("database latency timeout", namespace="mem", limit=1)
        citation = result.citations[0]

        self.assertEqual(citation.index_key, "mem:note:db:primary")
        self.assertEqual(citation.version, 2)
        self.assertEqual(citation.content_hash, "h-db-primary-v2")
        self.assertEqual(citation.metadata["revision"], 2)

    def test_duplicate_control_preserves_unique_relevance(self) -> None:
        self.index.index_document(
            _doc(
                document_id="db-duplicate",
                source_type="note",
                source_id="db:duplicate",
                content="Database latency incident caused by lock contention in orders table",
                content_hash="h-db-primary",
                metadata={"topic": "database-dup"},
            ),
            namespace="mem",
        )

        with_duplicates = self.engine.retrieve("database latency incident", namespace="mem", include_duplicates=True)
        without_duplicates = self.engine.retrieve(
            "database latency incident",
            namespace="mem",
            include_duplicates=False,
        )

        self.assertGreater(with_duplicates.returned, without_duplicates.returned)
        self.assertEqual(without_duplicates.matches[0].index_key, "mem:note:db:primary")

    def test_redaction_does_not_break_citation_identity(self) -> None:
        self.index.index_document(
            _doc(
                document_id="secure-note",
                source_type="note",
                source_id="secure:1",
                content="Escalate to admin@example.com token=critical-secret for incident handling",
                content_hash="h-secure",
                metadata={"email": "admin@example.com", "ticket": "SEC-12"},
            ),
            namespace="mem",
        )
        secure_engine = MemoryRetrievalEngine(self.index, privacy_filter=MemoryPrivacyFilter())

        result = secure_engine.retrieve("incident token", namespace="mem", limit=1)
        citation = result.citations[0]

        self.assertEqual(citation.index_key, "mem:note:secure:1")
        self.assertEqual(citation.source_id, "secure:1")
        self.assertNotIn("admin@example.com", citation.excerpt)
        self.assertEqual(citation.metadata["email"], "<REDACTED:EMAIL>")


if __name__ == "__main__":
    unittest.main()
