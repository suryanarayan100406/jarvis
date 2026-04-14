"""Indexing pipeline with deduplication and version tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from .ingestion_adapters import IngestedDocument


@dataclass(frozen=True)
class IndexedDocumentVersion:
    version: int
    document_id: str
    content_hash: str
    content: str
    metadata: dict[str, Any]
    indexed_at: str


@dataclass(frozen=True)
class IndexedDocumentRecord:
    index_key: str
    namespace: str
    source_type: str
    source_id: str
    current_document_id: str
    current_content_hash: str
    current_content: str
    current_metadata: dict[str, Any]
    current_version: int
    duplicate_of: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class IndexingResult:
    index_key: str
    action: str
    version: int
    duplicate_of: str | None


@dataclass(frozen=True)
class BatchIndexingSummary:
    total: int
    created: int
    updated: int
    unchanged: int
    deduplicated: int
    results: tuple[IndexingResult, ...]


class IndexingPipelineError(ValueError):
    """Raised when indexing operations receive invalid inputs."""


class MemoryIndexingPipeline:
    """Indexes ingested documents with content deduplication and version history."""

    def __init__(self) -> None:
        self._records: dict[str, IndexedDocumentRecord] = {}
        self._versions: dict[str, list[IndexedDocumentVersion]] = {}
        self._hash_primary_index: dict[str, str] = {}

    def index_document(
        self,
        document: IngestedDocument,
        *,
        namespace: str = "default",
    ) -> IndexingResult:
        normalized_namespace = _normalize_required(namespace, "namespace")
        normalized_source_type = _normalize_required(document.source_type, "source_type")
        normalized_source_id = _normalize_required(document.source_id, "source_id")
        normalized_content_hash = _normalize_required(document.content_hash, "content_hash")
        normalized_content = _normalize_content(document.content)
        metadata = dict(document.metadata)
        index_key = self._make_index_key(
            namespace=normalized_namespace,
            source_type=normalized_source_type,
            source_id=normalized_source_id,
        )

        existing = self._records.get(index_key)
        if existing is None:
            created = self._create_record(
                index_key=index_key,
                namespace=normalized_namespace,
                source_type=normalized_source_type,
                source_id=normalized_source_id,
                document=document,
                content_hash=normalized_content_hash,
                content=normalized_content,
                metadata=metadata,
            )
            action = "created"
            if created.duplicate_of is not None:
                action = "deduplicated"
            return IndexingResult(
                index_key=index_key,
                action=action,
                version=created.current_version,
                duplicate_of=created.duplicate_of,
            )

        if self._is_unchanged(existing, normalized_content_hash, metadata):
            return IndexingResult(
                index_key=index_key,
                action="unchanged",
                version=existing.current_version,
                duplicate_of=existing.duplicate_of,
            )

        updated = self._update_record(
            existing=existing,
            document=document,
            content_hash=normalized_content_hash,
            content=normalized_content,
            metadata=metadata,
        )
        action = "updated"
        if updated.duplicate_of is not None:
            action = "deduplicated"

        return IndexingResult(
            index_key=index_key,
            action=action,
            version=updated.current_version,
            duplicate_of=updated.duplicate_of,
        )

    def index_documents(
        self,
        documents: list[IngestedDocument] | tuple[IngestedDocument, ...],
        *,
        namespace: str = "default",
    ) -> BatchIndexingSummary:
        results = tuple(self.index_document(document, namespace=namespace) for document in documents)
        created = sum(1 for result in results if result.action == "created")
        updated = sum(1 for result in results if result.action == "updated")
        unchanged = sum(1 for result in results if result.action == "unchanged")
        deduplicated = sum(1 for result in results if result.action == "deduplicated")

        return BatchIndexingSummary(
            total=len(results),
            created=created,
            updated=updated,
            unchanged=unchanged,
            deduplicated=deduplicated,
            results=results,
        )

    def get_record(self, index_key: str) -> IndexedDocumentRecord:
        normalized_index_key = _normalize_required(index_key, "index_key")
        record = self._records.get(normalized_index_key)
        if record is None:
            raise KeyError(f"Unknown index key: {normalized_index_key}")
        return record

    def get_by_source(
        self,
        *,
        namespace: str,
        source_type: str,
        source_id: str,
    ) -> IndexedDocumentRecord:
        index_key = self._make_index_key(
            namespace=_normalize_required(namespace, "namespace"),
            source_type=_normalize_required(source_type, "source_type"),
            source_id=_normalize_required(source_id, "source_id"),
        )
        return self.get_record(index_key)

    def list_records(
        self,
        *,
        namespace: str | None = None,
        source_type: str | None = None,
        include_duplicates: bool = True,
    ) -> list[IndexedDocumentRecord]:
        normalized_namespace = _normalize_optional(namespace)
        normalized_source_type = _normalize_optional(source_type)
        records = list(self._records.values())

        if normalized_namespace is not None:
            records = [record for record in records if record.namespace == normalized_namespace]
        if normalized_source_type is not None:
            records = [record for record in records if record.source_type == normalized_source_type]
        if not include_duplicates:
            records = [record for record in records if record.duplicate_of is None]

        records.sort(key=lambda record: record.index_key)
        return records

    def list_versions(self, index_key: str) -> list[IndexedDocumentVersion]:
        normalized_index_key = _normalize_required(index_key, "index_key")
        versions = self._versions.get(normalized_index_key)
        if versions is None:
            raise KeyError(f"Unknown index key: {normalized_index_key}")
        return list(versions)

    def _create_record(
        self,
        *,
        index_key: str,
        namespace: str,
        source_type: str,
        source_id: str,
        document: IngestedDocument,
        content_hash: str,
        content: str,
        metadata: dict[str, Any],
    ) -> IndexedDocumentRecord:
        now = _utc_now_iso()
        duplicate_of = self._resolve_duplicate_target(index_key=index_key, content_hash=content_hash)

        record = IndexedDocumentRecord(
            index_key=index_key,
            namespace=namespace,
            source_type=source_type,
            source_id=source_id,
            current_document_id=document.document_id,
            current_content_hash=content_hash,
            current_content=content,
            current_metadata=dict(metadata),
            current_version=1,
            duplicate_of=duplicate_of,
            created_at=now,
            updated_at=now,
        )

        self._records[index_key] = record
        self._versions[index_key] = [
            IndexedDocumentVersion(
                version=1,
                document_id=document.document_id,
                content_hash=content_hash,
                content=content,
                metadata=dict(metadata),
                indexed_at=now,
            )
        ]
        return record

    def _update_record(
        self,
        *,
        existing: IndexedDocumentRecord,
        document: IngestedDocument,
        content_hash: str,
        content: str,
        metadata: dict[str, Any],
    ) -> IndexedDocumentRecord:
        now = _utc_now_iso()
        next_version = existing.current_version + 1
        duplicate_of = self._resolve_duplicate_target(index_key=existing.index_key, content_hash=content_hash)

        updated = IndexedDocumentRecord(
            index_key=existing.index_key,
            namespace=existing.namespace,
            source_type=existing.source_type,
            source_id=existing.source_id,
            current_document_id=document.document_id,
            current_content_hash=content_hash,
            current_content=content,
            current_metadata=dict(metadata),
            current_version=next_version,
            duplicate_of=duplicate_of,
            created_at=existing.created_at,
            updated_at=now,
        )

        self._records[existing.index_key] = updated
        self._versions[existing.index_key].append(
            IndexedDocumentVersion(
                version=next_version,
                document_id=document.document_id,
                content_hash=content_hash,
                content=content,
                metadata=dict(metadata),
                indexed_at=now,
            )
        )
        return updated

    def _resolve_duplicate_target(self, *, index_key: str, content_hash: str) -> str | None:
        primary = self._hash_primary_index.get(content_hash)
        if primary is None:
            self._hash_primary_index[content_hash] = index_key
            return None
        if primary == index_key:
            return None
        return primary

    @staticmethod
    def _is_unchanged(
        existing: IndexedDocumentRecord,
        content_hash: str,
        metadata: dict[str, Any],
    ) -> bool:
        return (
            existing.current_content_hash == content_hash
            and _stable_json_hash(existing.current_metadata) == _stable_json_hash(metadata)
        )

    @staticmethod
    def _make_index_key(*, namespace: str, source_type: str, source_id: str) -> str:
        return f"{namespace}:{source_type}:{source_id}"


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise IndexingPipelineError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _normalize_content(value: str) -> str:
    if not isinstance(value, str):
        raise IndexingPipelineError("document content must be a string")
    return " ".join(value.split())


def _stable_json_hash(value: dict[str, Any]) -> str:
    import json

    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
