"""Restore workflow with integrity checks for backup snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from .backup_strategy import BackupExecutionRecord, BackupSnapshot

RestoreStepStatus = Literal["restored", "failed", "skipped"]
RestoreStatus = Literal["completed", "failed"]


@dataclass(frozen=True)
class RestoreStepResult:
    dataset_id: str
    snapshot_id: str | None
    status: RestoreStepStatus
    integrity_verified: bool
    restored_bytes: int
    reason: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RestoreWorkflowResult:
    restore_id: str
    source_execution_id: str
    started_at: str
    completed_at: str
    status: RestoreStatus
    restored_count: int
    failed_count: int
    skipped_count: int
    deterministic_digest: str
    steps: tuple[RestoreStepResult, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "restore_id": self.restore_id,
            "source_execution_id": self.source_execution_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "restored_count": self.restored_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "deterministic_digest": self.deterministic_digest,
            "steps": [
                {
                    "dataset_id": step.dataset_id,
                    "snapshot_id": step.snapshot_id,
                    "status": step.status,
                    "integrity_verified": step.integrity_verified,
                    "restored_bytes": step.restored_bytes,
                    "reason": step.reason,
                    "metadata": dict(step.metadata),
                }
                for step in sorted(self.steps, key=lambda item: item.dataset_id)
            ],
            "metadata": dict(self.metadata),
        }


class RestoreWorkflowError(ValueError):
    """Raised when restore workflow inputs fail validation or integrity checks."""


class RestoreWorkflowEngine:
    """Restores datasets from backup records with strict integrity validation."""

    def restore_from_backup(
        self,
        backup_record: BackupExecutionRecord,
        payloads: dict[str, bytes | str],
        *,
        strict: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> RestoreWorkflowResult:
        if not isinstance(backup_record, BackupExecutionRecord):
            raise TypeError("backup_record must be BackupExecutionRecord")
        if backup_record.status != "completed":
            raise RestoreWorkflowError(
                f"backup_record status is {backup_record.status}; only completed backups can be restored"
            )

        normalized_payloads = _normalize_payloads(payloads)
        snapshots_by_dataset = {
            snapshot.dataset_id: snapshot for snapshot in backup_record.snapshots
        }

        started_at = _utc_now_iso()
        steps: list[RestoreStepResult] = []
        failed = False

        for dataset_id in sorted(snapshots_by_dataset):
            snapshot = snapshots_by_dataset[dataset_id]
            payload = normalized_payloads.get(dataset_id)

            if payload is None:
                step = RestoreStepResult(
                    dataset_id=dataset_id,
                    snapshot_id=snapshot.snapshot_id,
                    status="failed",
                    integrity_verified=False,
                    restored_bytes=0,
                    reason="Missing restore payload for dataset",
                    metadata={},
                )
                steps.append(step)
                failed = True
                if strict:
                    break
                continue

            expected_digest = snapshot.content_digest
            actual_digest = sha256(payload).hexdigest()
            if expected_digest != actual_digest:
                step = RestoreStepResult(
                    dataset_id=dataset_id,
                    snapshot_id=snapshot.snapshot_id,
                    status="failed",
                    integrity_verified=False,
                    restored_bytes=0,
                    reason="Integrity digest mismatch for restore payload",
                    metadata={
                        "expected_digest": expected_digest,
                        "actual_digest": actual_digest,
                    },
                )
                steps.append(step)
                failed = True
                if strict:
                    break
                continue

            steps.append(
                RestoreStepResult(
                    dataset_id=dataset_id,
                    snapshot_id=snapshot.snapshot_id,
                    status="restored",
                    integrity_verified=True,
                    restored_bytes=len(payload),
                    reason=None,
                    metadata={
                        "source_snapshot_created_at": snapshot.created_at,
                    },
                )
            )

        if not strict and len(steps) < len(snapshots_by_dataset):
            completed_dataset_ids = {step.dataset_id for step in steps}
            for dataset_id in sorted(snapshots_by_dataset):
                if dataset_id in completed_dataset_ids:
                    continue
                snapshot = snapshots_by_dataset[dataset_id]
                steps.append(
                    RestoreStepResult(
                        dataset_id=dataset_id,
                        snapshot_id=snapshot.snapshot_id,
                        status="skipped",
                        integrity_verified=False,
                        restored_bytes=0,
                        reason="Skipped due to earlier restore failure",
                        metadata={},
                    )
                )

        overall_status: RestoreStatus = "failed" if failed else "completed"
        completed_at = _utc_now_iso()
        deterministic_digest = _build_restore_digest(
            source_execution_id=backup_record.execution_id,
            status=overall_status,
            steps=steps,
        )
        restore_id = f"restore-{deterministic_digest[:20]}"

        return RestoreWorkflowResult(
            restore_id=restore_id,
            source_execution_id=backup_record.execution_id,
            started_at=started_at,
            completed_at=completed_at,
            status=overall_status,
            restored_count=sum(1 for step in steps if step.status == "restored"),
            failed_count=sum(1 for step in steps if step.status == "failed"),
            skipped_count=sum(1 for step in steps if step.status == "skipped"),
            deterministic_digest=deterministic_digest,
            steps=tuple(sorted(steps, key=lambda item: item.dataset_id)),
            metadata=dict(metadata or {}),
        )

    def verify_restore_payload(
        self,
        snapshot: BackupSnapshot,
        payload: bytes | str,
    ) -> bool:
        if not isinstance(snapshot, BackupSnapshot):
            raise TypeError("snapshot must be BackupSnapshot")

        normalized_payload = _normalize_payload(payload, snapshot.dataset_id)
        return sha256(normalized_payload).hexdigest() == snapshot.content_digest


def _normalize_payloads(payloads: dict[str, bytes | str]) -> dict[str, bytes]:
    if not isinstance(payloads, dict):
        raise TypeError("payloads must be dict[str, bytes|str]")

    normalized: dict[str, bytes] = {}
    for dataset_id, payload in payloads.items():
        normalized_dataset_id = _normalize_required(dataset_id, "dataset_id").lower()
        normalized[normalized_dataset_id] = _normalize_payload(payload, normalized_dataset_id)
    return normalized


def _normalize_payload(payload: bytes | str, dataset_id: str) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    raise TypeError(f"payload for dataset {dataset_id} must be bytes or str")


def _build_restore_digest(
    *,
    source_execution_id: str,
    status: RestoreStatus,
    steps: list[RestoreStepResult],
) -> str:
    canonical = json.dumps(
        {
            "source_execution_id": source_execution_id,
            "status": status,
            "steps": [
                {
                    "dataset_id": step.dataset_id,
                    "snapshot_id": step.snapshot_id,
                    "status": step.status,
                    "integrity_verified": step.integrity_verified,
                    "restored_bytes": step.restored_bytes,
                    "reason": step.reason,
                }
                for step in sorted(steps, key=lambda item: item.dataset_id)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise RestoreWorkflowError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "RestoreStepStatus",
    "RestoreStatus",
    "RestoreStepResult",
    "RestoreWorkflowResult",
    "RestoreWorkflowError",
    "RestoreWorkflowEngine",
]