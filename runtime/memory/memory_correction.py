"""User-correctable memory update workflow with auditable correction records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

from .indexing_pipeline import MemoryIndexingPipeline
from .ingestion_adapters import IngestedDocument


@dataclass(frozen=True)
class MemoryCorrectionRecord:
    correction_id: str
    index_key: str
    namespace: str
    source_type: str
    source_id: str
    actor_id: str
    reason: str
    previous_version: int
    updated_version: int
    previous_content_hash: str
    updated_content_hash: str
    action: str
    metadata: dict[str, Any]
    requested_at: str


class MemoryCorrectionError(ValueError):
    """Raised when memory correction requests violate workflow constraints."""


class MemoryCorrectionWorkflow:
    """Applies user-provided corrections to indexed memory with explicit audit trace."""

    def __init__(self, index: MemoryIndexingPipeline) -> None:
        self.index = index
        self._corrections: dict[str, MemoryCorrectionRecord] = {}

    def apply_correction(
        self,
        *,
        namespace: str,
        source_type: str,
        source_id: str,
        corrected_content: str,
        actor_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
        preserve_existing_metadata: bool = True,
    ) -> MemoryCorrectionRecord:
        normalized_namespace = _normalize_required(namespace, "namespace")
        normalized_source_type = _normalize_required(source_type, "source_type")
        normalized_source_id = _normalize_required(source_id, "source_id")
        normalized_actor = _normalize_required(actor_id, "actor_id")
        normalized_reason = _normalize_required(reason, "reason")
        normalized_content = _normalize_content(corrected_content)
        if not normalized_content:
            raise MemoryCorrectionError("corrected_content is required")

        try:
            existing = self.index.get_by_source(
                namespace=normalized_namespace,
                source_type=normalized_source_type,
                source_id=normalized_source_id,
            )
        except KeyError as exc:
            raise MemoryCorrectionError(
                f"Unknown memory source: {normalized_namespace}:{normalized_source_type}:{normalized_source_id}"
            ) from exc

        correction_id = str(uuid4())
        requested_at = _utc_now_iso()
        correction_metadata = dict(metadata or {})

        if normalized_content == existing.current_content and not correction_metadata:
            record = MemoryCorrectionRecord(
                correction_id=correction_id,
                index_key=existing.index_key,
                namespace=normalized_namespace,
                source_type=existing.source_type,
                source_id=existing.source_id,
                actor_id=normalized_actor,
                reason=normalized_reason,
                previous_version=existing.current_version,
                updated_version=existing.current_version,
                previous_content_hash=existing.current_content_hash,
                updated_content_hash=existing.current_content_hash,
                action="unchanged",
                metadata={
                    "correction_id": correction_id,
                    "corrected_by": normalized_actor,
                    "correction_reason": normalized_reason,
                    "requested_at": requested_at,
                    "preserve_existing_metadata": preserve_existing_metadata,
                },
                requested_at=requested_at,
            )
            self._corrections[record.correction_id] = record
            return record

        merged_metadata = self._merge_metadata(
            existing_metadata=existing.current_metadata,
            correction_metadata=correction_metadata,
            correction_id=correction_id,
            actor_id=normalized_actor,
            reason=normalized_reason,
            requested_at=requested_at,
            previous_content_hash=existing.current_content_hash,
            previous_version=existing.current_version,
            preserve_existing_metadata=preserve_existing_metadata,
        )

        corrected_document = IngestedDocument(
            document_id=_hash_text(
                f"correction:{existing.index_key}:{correction_id}:{_hash_text(normalized_content)}"
            ),
            source_type=existing.source_type,
            source_id=existing.source_id,
            content=normalized_content,
            content_hash=_hash_text(normalized_content),
            metadata=merged_metadata,
            ingested_at=requested_at,
        )
        indexing_result = self.index.index_document(corrected_document, namespace=normalized_namespace)
        updated = self.index.get_record(existing.index_key)

        record = MemoryCorrectionRecord(
            correction_id=correction_id,
            index_key=updated.index_key,
            namespace=updated.namespace,
            source_type=updated.source_type,
            source_id=updated.source_id,
            actor_id=normalized_actor,
            reason=normalized_reason,
            previous_version=existing.current_version,
            updated_version=updated.current_version,
            previous_content_hash=existing.current_content_hash,
            updated_content_hash=updated.current_content_hash,
            action=indexing_result.action,
            metadata=dict(updated.current_metadata),
            requested_at=requested_at,
        )
        self._corrections[record.correction_id] = record
        return record

    def get_correction(self, correction_id: str) -> MemoryCorrectionRecord:
        normalized_id = _normalize_required(correction_id, "correction_id")
        record = self._corrections.get(normalized_id)
        if record is None:
            raise KeyError(f"Unknown correction_id: {normalized_id}")
        return record

    def list_corrections(
        self,
        *,
        index_key: str | None = None,
        actor_id: str | None = None,
        action: str | None = None,
    ) -> list[MemoryCorrectionRecord]:
        normalized_index_key = _normalize_optional(index_key)
        normalized_actor = _normalize_optional(actor_id)
        normalized_action = _normalize_optional(action)

        records = list(self._corrections.values())
        if normalized_index_key is not None:
            records = [record for record in records if record.index_key == normalized_index_key]
        if normalized_actor is not None:
            records = [record for record in records if record.actor_id == normalized_actor]
        if normalized_action is not None:
            records = [record for record in records if record.action == normalized_action]

        records.sort(key=lambda record: (record.requested_at, record.correction_id))
        return records

    @staticmethod
    def _merge_metadata(
        *,
        existing_metadata: dict[str, Any],
        correction_metadata: dict[str, Any],
        correction_id: str,
        actor_id: str,
        reason: str,
        requested_at: str,
        previous_content_hash: str,
        previous_version: int,
        preserve_existing_metadata: bool,
    ) -> dict[str, Any]:
        base_metadata = dict(existing_metadata) if preserve_existing_metadata else {}
        merged = dict(base_metadata)
        merged.update(correction_metadata)
        merged.update(
            {
                "correction_id": correction_id,
                "corrected_by": actor_id,
                "correction_reason": reason,
                "requested_at": requested_at,
                "previous_content_hash": previous_content_hash,
                "previous_version": previous_version,
            }
        )
        return merged


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise MemoryCorrectionError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _normalize_content(content: str) -> str:
    if not isinstance(content, str):
        raise MemoryCorrectionError("corrected_content must be a string")
    return " ".join(content.split())


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "MemoryCorrectionError",
    "MemoryCorrectionRecord",
    "MemoryCorrectionWorkflow",
]
