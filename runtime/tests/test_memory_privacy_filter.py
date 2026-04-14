"""Tests for P5-T10 memory privacy filters and redaction-aware retrieval."""

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
        ingested_at="2026-04-14T17:00:00Z",
    )


class MemoryPrivacyFilterTests(unittest.TestCase):
    def test_redact_text_masks_common_sensitive_patterns(self) -> None:
        filter_ = MemoryPrivacyFilter()

        result = filter_.redact_text(
            "Contact admin@example.com token=supersecret at 10.20.30.40 with key AKIA1234567890ABCDEF"
        )

        self.assertNotIn("admin@example.com", result.redacted_text)
        self.assertNotIn("supersecret", result.redacted_text)
        self.assertNotIn("10.20.30.40", result.redacted_text)
        self.assertNotIn("AKIA1234567890ABCDEF", result.redacted_text)
        self.assertGreaterEqual(result.redaction_count, 4)

    def test_redact_metadata_masks_sensitive_keys(self) -> None:
        filter_ = MemoryPrivacyFilter()

        metadata = filter_.redact_metadata(
            {
                "password": "hunter2",
                "owner": "ops@example.com",
                "notes": "token=abc123",
            }
        )

        self.assertEqual(metadata["password"], "<REDACTED:PASSWORD>")
        self.assertNotIn("ops@example.com", metadata["owner"])
        self.assertNotIn("abc123", metadata["notes"])


class RedactionAwareRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = MemoryIndexingPipeline()
        self.index.index_document(
            _doc(
                document_id="d1",
                source_type="note",
                source_id="ops:1",
                content="Escalate incident to admin@example.com with token=topsecret",
                content_hash="h1",
                metadata={
                    "email": "admin@example.com",
                    "ticket": "INC-123",
                    "note": "password=ops-secret",
                },
            ),
            namespace="mem",
        )

    def test_retrieval_without_filter_returns_unredacted_excerpt(self) -> None:
        engine = MemoryRetrievalEngine(self.index)

        result = engine.retrieve("incident token", namespace="mem")

        self.assertEqual(result.returned, 1)
        self.assertIn("admin@example.com", result.matches[0].excerpt)
        self.assertEqual(result.citations[0].metadata["email"], "admin@example.com")

    def test_retrieval_with_filter_redacts_excerpt_and_metadata(self) -> None:
        engine = MemoryRetrievalEngine(self.index, privacy_filter=MemoryPrivacyFilter())

        result = engine.retrieve("incident token", namespace="mem")

        self.assertEqual(result.returned, 1)
        self.assertNotIn("admin@example.com", result.matches[0].excerpt)
        self.assertNotIn("topsecret", result.matches[0].excerpt)
        self.assertEqual(result.citations[0].metadata["email"], "<REDACTED:EMAIL>")
        self.assertNotIn("ops-secret", result.citations[0].metadata["note"])
        self.assertIn("_excerpt_redactions", result.citations[0].metadata)


if __name__ == "__main__":
    unittest.main()
