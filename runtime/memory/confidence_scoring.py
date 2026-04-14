"""Confidence scoring and evidence ranking for retrieval outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .retrieval_engine import RetrievalCitation, RetrievalResult

ConfidenceBand = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class RankedEvidence:
    rank: int
    index_key: str
    source_type: str
    retrieval_score: float
    evidence_score: float
    excerpt: str
    citation: RetrievalCitation


@dataclass(frozen=True)
class ConfidenceScoredResult:
    query: str
    confidence_score: float
    confidence_band: ConfidenceBand
    rationale: str
    ranked_evidence: tuple[RankedEvidence, ...]
    citations: tuple[RetrievalCitation, ...]
    answer_context: str


class ConfidenceScoringError(ValueError):
    """Raised when confidence scoring inputs are invalid."""


class MemoryConfidenceScorer:
    """Assigns confidence and ranks evidence using retrieval strength plus source reliability."""

    default_source_reliability = {
        "log": 0.92,
        "file": 0.85,
        "note": 0.78,
        "command_history": 0.74,
    }

    def __init__(self, source_reliability: dict[str, float] | None = None) -> None:
        reliability = dict(self.default_source_reliability)
        if source_reliability is not None:
            reliability.update(source_reliability)

        for source_type, weight in reliability.items():
            if weight < 0 or weight > 1:
                raise ConfidenceScoringError(
                    f"Source reliability weight must be between 0 and 1 for {source_type}"
                )

        self.source_reliability = reliability

    def assess(
        self,
        retrieval: RetrievalResult,
        *,
        max_evidence: int = 5,
    ) -> ConfidenceScoredResult:
        if max_evidence < 1:
            raise ConfidenceScoringError("max_evidence must be at least 1")

        ranked = self._rank_evidence(retrieval, max_evidence=max_evidence)
        confidence = self._compute_confidence_score(ranked, max_evidence=max_evidence)
        band = self._band_for_score(confidence)
        rationale = self._build_rationale(band, confidence, ranked)
        citations = tuple(item.citation for item in ranked)

        answer_context = "\n".join(
            f"[{entry.rank}] {entry.excerpt}" for entry in ranked
        )

        return ConfidenceScoredResult(
            query=retrieval.query,
            confidence_score=confidence,
            confidence_band=band,
            rationale=rationale,
            ranked_evidence=ranked,
            citations=citations,
            answer_context=answer_context,
        )

    def _rank_evidence(
        self,
        retrieval: RetrievalResult,
        *,
        max_evidence: int,
    ) -> tuple[RankedEvidence, ...]:
        scored: list[tuple[float, int, RetrievalCitation, float]] = []

        for index, match in enumerate(retrieval.matches):
            source_weight = self.source_reliability.get(match.citation.source_type, 0.70)
            evidence_score = round(_clamp(match.score) * source_weight, 6)
            scored.append((evidence_score, index, match.citation, match.score))

        scored.sort(key=lambda item: (-item[0], -item[3], item[2].index_key))
        limited = scored[:max_evidence]

        ranked: list[RankedEvidence] = []
        for rank, (evidence_score, _original_index, citation, retrieval_score) in enumerate(limited, start=1):
            ranked.append(
                RankedEvidence(
                    rank=rank,
                    index_key=citation.index_key,
                    source_type=citation.source_type,
                    retrieval_score=round(_clamp(retrieval_score), 6),
                    evidence_score=evidence_score,
                    excerpt=citation.excerpt,
                    citation=citation,
                )
            )

        return tuple(ranked)

    def _compute_confidence_score(
        self,
        ranked: tuple[RankedEvidence, ...],
        *,
        max_evidence: int,
    ) -> float:
        if not ranked:
            return 0.0

        evidence_scores = [item.evidence_score for item in ranked]
        top = max(evidence_scores)
        avg = sum(evidence_scores) / len(evidence_scores)
        support = min(1.0, len(ranked) / max_evidence)
        diversity = len({item.source_type for item in ranked}) / len(ranked)

        corroboration_bonus = min(0.30, 0.12 * max(0, len(ranked) - 1))
        score = (
            (top * 0.50)
            + (avg * 0.20)
            + (support * 0.10)
            + (diversity * 0.10)
            + corroboration_bonus
        )

        # One evidence item can be strong but should not be treated as high confidence alone.
        if len(ranked) < 2:
            score = min(score, 0.74)

        return round(_clamp(score), 6)

    @staticmethod
    def _band_for_score(score: float) -> ConfidenceBand:
        if score >= 0.75:
            return "high"
        if score >= 0.45:
            return "medium"
        return "low"

    @staticmethod
    def _build_rationale(
        band: ConfidenceBand,
        score: float,
        ranked: tuple[RankedEvidence, ...],
    ) -> str:
        if not ranked:
            return "No supporting evidence matched the query."

        if band == "high":
            return (
                f"High confidence ({score:.3f}) with {len(ranked)} corroborating evidence items "
                f"across {len({item.source_type for item in ranked})} source types."
            )

        if band == "medium":
            return (
                f"Medium confidence ({score:.3f}); evidence exists but coverage or corroboration is limited "
                f"to {len(ranked)} ranked items."
            )

        return (
            f"Low confidence ({score:.3f}); matched evidence is weak or sparse "
            f"with only {len(ranked)} ranked items."
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
