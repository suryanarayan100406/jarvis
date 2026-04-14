"""Multimodal evidence store tied to the memory indexing system."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256

from runtime.memory import IndexedDocumentRecord, IngestedDocument, IndexingResult, MemoryIndexingPipeline

from .summary_extractor import MultimodalSummaryCitation, MultimodalSummaryResult


@dataclass(frozen=True)
class MultimodalEvidenceReference:
    index_key: str
    source_type: str
    source_id: str
    action: str
    version: int
    duplicate_of: str | None


@dataclass(frozen=True)
class MultimodalEvidenceStoreResult:
    scene_id: str
    namespace: str
    summary_reference: MultimodalEvidenceReference
    citation_references: tuple[MultimodalEvidenceReference, ...]
    bundle_reference: MultimodalEvidenceReference
    stored_at: str


@dataclass(frozen=True)
class MultimodalEvidenceBundle:
    scene_id: str
    namespace: str
    summary_record: IndexedDocumentRecord | None
    citation_records: tuple[IndexedDocumentRecord, ...]
    bundle_record: IndexedDocumentRecord | None


class MultimodalEvidenceStoreError(ValueError):
    """Raised when multimodal evidence persistence receives invalid input."""


class MultimodalEvidenceStore:
    """Persists multimodal summaries and citations into the memory index."""

    summary_source_type = "multimodal_summary"
    citation_source_type = "multimodal_citation"
    bundle_source_type = "multimodal_evidence_bundle"

    def __init__(self, index: MemoryIndexingPipeline, *, default_namespace: str = "multimodal") -> None:
        if not isinstance(index, MemoryIndexingPipeline):
            raise MultimodalEvidenceStoreError("index must be a MemoryIndexingPipeline")

        self.index = index
        self.default_namespace = _normalize_required(default_namespace, "default_namespace")

    def store_summary(
        self,
        summary: MultimodalSummaryResult,
        *,
        namespace: str | None = None,
    ) -> MultimodalEvidenceStoreResult:
        _validate_summary(summary)

        target_namespace = self.default_namespace if namespace is None else _normalize_required(namespace, "namespace")
        summary_doc = _summary_document(summary)
        summary_result = self.index.index_document(summary_doc, namespace=target_namespace)

        citation_refs: list[MultimodalEvidenceReference] = []
        citation_index_keys: list[str] = []
        for citation in summary.citations:
            citation_doc = _citation_document(summary, citation)
            citation_result = self.index.index_document(citation_doc, namespace=target_namespace)
            citation_index_keys.append(citation_result.index_key)
            citation_refs.append(_to_reference(citation_result, citation_doc.source_type, citation_doc.source_id))

        bundle_doc = _bundle_document(summary, summary_result.index_key, tuple(citation_index_keys))
        bundle_result = self.index.index_document(bundle_doc, namespace=target_namespace)

        return MultimodalEvidenceStoreResult(
            scene_id=summary.scene_id,
            namespace=target_namespace,
            summary_reference=_to_reference(summary_result, summary_doc.source_type, summary_doc.source_id),
            citation_references=tuple(citation_refs),
            bundle_reference=_to_reference(bundle_result, bundle_doc.source_type, bundle_doc.source_id),
            stored_at=_utc_now_iso(),
        )

    def load_scene_evidence(
        self,
        scene_id: str,
        *,
        namespace: str | None = None,
        include_duplicates: bool = True,
    ) -> MultimodalEvidenceBundle:
        normalized_scene_id = _normalize_required(scene_id, "scene_id")
        target_namespace = self.default_namespace if namespace is None else _normalize_required(namespace, "namespace")

        records = self.index.list_records(namespace=target_namespace, include_duplicates=include_duplicates)

        summary_candidates = [
            record
            for record in records
            if record.source_type == self.summary_source_type and record.current_metadata.get("scene_id") == normalized_scene_id
        ]
        citation_candidates = [
            record
            for record in records
            if record.source_type == self.citation_source_type and record.current_metadata.get("scene_id") == normalized_scene_id
        ]
        bundle_candidates = [
            record
            for record in records
            if record.source_type == self.bundle_source_type and record.current_metadata.get("scene_id") == normalized_scene_id
        ]

        summary_record = _latest_record(summary_candidates)
        bundle_record = _latest_record(bundle_candidates)
        citation_records = tuple(sorted(citation_candidates, key=lambda record: record.index_key))

        return MultimodalEvidenceBundle(
            scene_id=normalized_scene_id,
            namespace=target_namespace,
            summary_record=summary_record,
            citation_records=citation_records,
            bundle_record=bundle_record,
        )


def _summary_document(summary: MultimodalSummaryResult) -> IngestedDocument:
    source_id = f"{summary.scene_id}:{summary.summary_id}"
    content = _normalize_content(summary.summary_text)
    metadata = {
        "scene_id": summary.scene_id,
        "summary_id": summary.summary_id,
        "key_points": list(summary.key_points),
        "language_hint": summary.language_hint,
        "overall_confidence": summary.overall_confidence,
        "warning_count": len(summary.warnings),
        "warnings": list(summary.warnings),
        "citation_ids": [citation.citation_id for citation in summary.citations],
    }
    return IngestedDocument(
        document_id=_hash_text(f"summary:{source_id}:{content}"),
        source_type=MultimodalEvidenceStore.summary_source_type,
        source_id=source_id,
        content=content,
        content_hash=_hash_text(content),
        metadata=metadata,
        ingested_at=summary.generated_at,
    )


def _citation_document(summary: MultimodalSummaryResult, citation: MultimodalSummaryCitation) -> IngestedDocument:
    source_id = f"{summary.summary_id}:{citation.citation_id}"
    content = _normalize_content(citation.excerpt)
    metadata = {
        "scene_id": summary.scene_id,
        "summary_id": summary.summary_id,
        "citation_id": citation.citation_id,
        "evidence_source_type": citation.source_type,
        "evidence_source_id": citation.source_id,
        "evidence_confidence": citation.confidence,
        "evidence_metadata": dict(citation.metadata),
    }
    return IngestedDocument(
        document_id=_hash_text(f"citation:{source_id}:{content}"),
        source_type=MultimodalEvidenceStore.citation_source_type,
        source_id=source_id,
        content=content,
        content_hash=_hash_text(content),
        metadata=metadata,
        ingested_at=summary.generated_at,
    )


def _bundle_document(
    summary: MultimodalSummaryResult,
    summary_index_key: str,
    citation_index_keys: tuple[str, ...],
) -> IngestedDocument:
    source_id = summary.summary_id
    content = _normalize_content(
        f"scene={summary.scene_id} summary={summary.summary_id} citations={len(citation_index_keys)}"
    )
    metadata = {
        "scene_id": summary.scene_id,
        "summary_id": summary.summary_id,
        "summary_index_key": summary_index_key,
        "citation_index_keys": list(citation_index_keys),
        "citation_count": len(citation_index_keys),
        "overall_confidence": summary.overall_confidence,
    }
    return IngestedDocument(
        document_id=_hash_text(f"bundle:{source_id}:{content}:{','.join(citation_index_keys)}"),
        source_type=MultimodalEvidenceStore.bundle_source_type,
        source_id=source_id,
        content=content,
        content_hash=_hash_text(content),
        metadata=metadata,
        ingested_at=summary.generated_at,
    )


def _to_reference(result: IndexingResult, source_type: str, source_id: str) -> MultimodalEvidenceReference:
    return MultimodalEvidenceReference(
        index_key=result.index_key,
        source_type=source_type,
        source_id=source_id,
        action=result.action,
        version=result.version,
        duplicate_of=result.duplicate_of,
    )


def _latest_record(records: list[IndexedDocumentRecord]) -> IndexedDocumentRecord | None:
    if not records:
        return None
    return max(records, key=lambda record: (record.updated_at, record.current_version, record.index_key))


def _validate_summary(summary: MultimodalSummaryResult) -> None:
    if not isinstance(summary, MultimodalSummaryResult):
        raise MultimodalEvidenceStoreError("summary must be a MultimodalSummaryResult")
    _normalize_required(summary.scene_id, "summary.scene_id")
    _normalize_required(summary.summary_id, "summary.summary_id")


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise MultimodalEvidenceStoreError(f"{field_name} is required")
    return normalized


def _normalize_content(value: str) -> str:
    return " ".join(str(value).split())


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "MultimodalEvidenceBundle",
    "MultimodalEvidenceReference",
    "MultimodalEvidenceStore",
    "MultimodalEvidenceStoreError",
    "MultimodalEvidenceStoreResult",
]
