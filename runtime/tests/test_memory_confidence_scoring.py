"""Tests for P5-T5 confidence scoring and evidence ranking."""

from __future__ import annotations

import unittest

from runtime.memory import (
    IngestedDocument,
    MemoryConfidenceScorer,
    MemoryIndexingPipeline,
    MemoryRetrievalEngine,
    RetrievalCitation,
    RetrievalMatch,
    RetrievalResult,
)


class MemoryConfidenceScorerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scorer = MemoryConfidenceScorer()

    def test_low_confidence_when_no_matches(self) -> None:
        retrieval = RetrievalResult(
            query="unmatched query",
            total_candidates=0,
            returned=0,
            answer_context="",
            matches=(),
            citations=(),
            searched_at="2026-04-14T13:30:00Z",
        )

        scored = self.scorer.assess(retrieval)

        self.assertEqual(scored.confidence_score, 0.0)
        self.assertEqual(scored.confidence_band, "low")
        self.assertEqual(len(scored.ranked_evidence), 0)

    def test_evidence_ranking_respects_source_reliability(self) -> None:
        note_citation = RetrievalCitation(
            citation_id="cite-note",
            index_key="mem:note:1",
            source_type="note",
            source_id="note:1",
            version=1,
            content_hash="h-note",
            excerpt="note evidence",
            metadata={},
        )
        log_citation = RetrievalCitation(
            citation_id="cite-log",
            index_key="mem:log:1",
            source_type="log",
            source_id="log:1",
            version=1,
            content_hash="h-log",
            excerpt="log evidence",
            metadata={},
        )

        retrieval = RetrievalResult(
            query="evidence",
            total_candidates=2,
            returned=2,
            answer_context="",
            matches=(
                RetrievalMatch(
                    index_key="mem:note:1",
                    score=0.90,
                    version=1,
                    excerpt="note evidence",
                    citation=note_citation,
                ),
                RetrievalMatch(
                    index_key="mem:log:1",
                    score=0.82,
                    version=1,
                    excerpt="log evidence",
                    citation=log_citation,
                ),
            ),
            citations=(note_citation, log_citation),
            searched_at="2026-04-14T13:30:00Z",
        )

        scored = self.scorer.assess(retrieval)

        self.assertEqual(scored.ranked_evidence[0].source_type, "log")
        self.assertGreater(
            scored.ranked_evidence[0].evidence_score,
            scored.ranked_evidence[1].evidence_score,
        )

    def test_single_strong_evidence_caps_to_medium(self) -> None:
        citation = RetrievalCitation(
            citation_id="cite-single",
            index_key="mem:note:single",
            source_type="log",
            source_id="log:single",
            version=1,
            content_hash="h-single",
            excerpt="single evidence",
            metadata={},
        )
        retrieval = RetrievalResult(
            query="single",
            total_candidates=1,
            returned=1,
            answer_context="",
            matches=(
                RetrievalMatch(
                    index_key="mem:note:single",
                    score=0.98,
                    version=1,
                    excerpt="single evidence",
                    citation=citation,
                ),
            ),
            citations=(citation,),
            searched_at="2026-04-14T13:30:00Z",
        )

        scored = self.scorer.assess(retrieval)

        self.assertEqual(scored.confidence_band, "medium")
        self.assertLess(scored.confidence_score, 0.75)

    def test_high_confidence_with_multiple_strong_evidence(self) -> None:
        index = MemoryIndexingPipeline()
        engine = MemoryRetrievalEngine(index)

        index.index_document(
            IngestedDocument(
                document_id="d1",
                source_type="log",
                source_id="sys:1",
                content="deployment rollback succeeded with healthy services",
                content_hash="h1",
                metadata={"topic": "rollback"},
                ingested_at="2026-04-14T13:00:00Z",
            ),
            namespace="mem",
        )
        index.index_document(
            IngestedDocument(
                document_id="d2",
                source_type="file",
                source_id="reports:1",
                content="deployment report confirms rollback and service health",
                content_hash="h2",
                metadata={"topic": "report"},
                ingested_at="2026-04-14T13:00:00Z",
            ),
            namespace="mem",
        )
        index.index_document(
            IngestedDocument(
                document_id="d3",
                source_type="note",
                source_id="ops:1",
                content="operator note: rollback successful and latency normal",
                content_hash="h3",
                metadata={"topic": "ops"},
                ingested_at="2026-04-14T13:00:00Z",
            ),
            namespace="mem",
        )

        retrieval = engine.retrieve("deployment rollback service health", namespace="mem", limit=3)
        scored = self.scorer.assess(retrieval)

        self.assertEqual(scored.confidence_band, "high")
        self.assertGreaterEqual(scored.confidence_score, 0.75)
        self.assertEqual(len(scored.citations), len(scored.ranked_evidence))
        self.assertTrue(scored.answer_context.startswith("[1]"))


if __name__ == "__main__":
    unittest.main()
