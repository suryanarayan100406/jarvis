"""Retrieval engine with source citation binding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .indexing_pipeline import IndexedDocumentRecord, IndexedDocumentVersion, MemoryIndexingPipeline
from .privacy_filter import MemoryPrivacyFilter


@dataclass(frozen=True)
class RetrievalCitation:
    citation_id: str
    index_key: str
    source_type: str
    source_id: str
    version: int
    content_hash: str
    excerpt: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RetrievalMatch:
    index_key: str
    score: float
    version: int
    excerpt: str
    citation: RetrievalCitation


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    total_candidates: int
    returned: int
    answer_context: str
    matches: tuple[RetrievalMatch, ...]
    citations: tuple[RetrievalCitation, ...]
    searched_at: str


class RetrievalEngineError(ValueError):
    """Raised when retrieval requests or indexed records are invalid."""


class MemoryRetrievalEngine:
    """Retrieves indexed records and binds ranked results to explicit citations."""

    def __init__(
        self,
        index: MemoryIndexingPipeline,
        privacy_filter: MemoryPrivacyFilter | None = None,
    ) -> None:
        self.index = index
        self.privacy_filter = privacy_filter

    def retrieve(
        self,
        query: str,
        *,
        namespace: str = "default",
        limit: int = 5,
        include_duplicates: bool = False,
        min_score: float = 0.0,
    ) -> RetrievalResult:
        normalized_query = _normalize_required(query, "query")
        if limit < 1:
            raise RetrievalEngineError("limit must be at least 1")
        if min_score < 0:
            raise RetrievalEngineError("min_score must be non-negative")

        tokens = _tokenize(normalized_query)
        records = self.index.list_records(
            namespace=namespace,
            include_duplicates=include_duplicates,
        )

        scored: list[RetrievalMatch] = []
        for record in records:
            match = self._score_record(record, tokens)
            if match is None:
                continue
            if match.score < min_score:
                continue
            scored.append(match)

        scored.sort(key=lambda item: (-item.score, item.index_key))
        limited = tuple(scored[:limit])
        citations = tuple(match.citation for match in limited)

        answer_context = "\n".join(
            f"[{index + 1}] {match.excerpt}" for index, match in enumerate(limited)
        )

        return RetrievalResult(
            query=normalized_query,
            total_candidates=len(scored),
            returned=len(limited),
            answer_context=answer_context,
            matches=limited,
            citations=citations,
            searched_at=_utc_now_iso(),
        )

    def _score_record(self, record: IndexedDocumentRecord, query_tokens: list[str]) -> RetrievalMatch | None:
        if not query_tokens:
            return None

        haystack = f"{record.current_content} {record.source_id}"
        haystack_tokens = _tokenize(haystack)
        if not haystack_tokens:
            return None

        matched_tokens = [token for token in query_tokens if token in haystack_tokens]
        if not matched_tokens:
            return None

        # Score combines query coverage with term density in the indexed text.
        coverage = len(set(matched_tokens)) / len(set(query_tokens))
        density = len(matched_tokens) / max(1, len(haystack_tokens))
        score = round((coverage * 0.8) + (density * 0.2), 6)

        version = self._latest_version(record.index_key)
        excerpt = _excerpt(record.current_content, matched_tokens)
        citation_metadata = dict(version.metadata)

        if self.privacy_filter is not None:
            redaction = self.privacy_filter.redact_text(excerpt)
            excerpt = redaction.redacted_text
            citation_metadata = self.privacy_filter.redact_metadata(citation_metadata)
            if redaction.redaction_count > 0:
                citation_metadata["_excerpt_redactions"] = redaction.redaction_count
                citation_metadata["_excerpt_redaction_categories"] = redaction.categories

        citation = RetrievalCitation(
            citation_id=f"cite-{record.index_key}:{version.version}",
            index_key=record.index_key,
            source_type=record.source_type,
            source_id=record.source_id,
            version=version.version,
            content_hash=version.content_hash,
            excerpt=excerpt,
            metadata=citation_metadata,
        )

        return RetrievalMatch(
            index_key=record.index_key,
            score=score,
            version=version.version,
            excerpt=excerpt,
            citation=citation,
        )

    def _latest_version(self, index_key: str) -> IndexedDocumentVersion:
        versions = self.index.list_versions(index_key)
        if not versions:
            raise RetrievalEngineError(f"Indexed record has no versions: {index_key}")
        return versions[-1]


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
    return [token for token in normalized.split() if token]


def _excerpt(content: str, matched_tokens: list[str], *, max_chars: int = 180) -> str:
    normalized = _normalize_content(content)
    if not normalized:
        return ""

    lower = normalized.lower()
    positions = [lower.find(token) for token in matched_tokens if lower.find(token) >= 0]
    if not positions:
        return normalized[:max_chars]

    start = max(0, min(positions) - 30)
    end = min(len(normalized), start + max_chars)
    excerpt = normalized[start:end]
    if start > 0:
        excerpt = f"...{excerpt}"
    if end < len(normalized):
        excerpt = f"{excerpt}..."
    return excerpt


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise RetrievalEngineError(f"{field_name} is required")
    return normalized


def _normalize_content(value: str) -> str:
    return " ".join(value.split())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
