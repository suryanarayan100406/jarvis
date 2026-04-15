"""Backup strategy definitions for state, memory, and configuration assets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

BackupStatus = Literal["completed", "failed"]

_REQUIRED_DATASET_IDS = {"state", "memory", "configuration"}
_ALLOWED_INTEGRITY_ALGORITHMS = {"sha256"}


@dataclass(frozen=True)
class BackupDatasetPolicy:
    dataset_id: str
    source_path: str
    cadence_minutes: int
    retention_points: int
    encryption_required: bool
    integrity_algorithm: str
    max_snapshot_bytes: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class BackupStrategyProfile:
    profile_id: str
    strategy_version: str
    created_at: str
    dataset_policies: tuple[BackupDatasetPolicy, ...]
    metadata: dict[str, Any]

    def get_policy(self, dataset_id: str) -> BackupDatasetPolicy:
        normalized_dataset_id = _normalize_required(dataset_id, "dataset_id").lower()
        for policy in self.dataset_policies:
            if policy.dataset_id == normalized_dataset_id:
                return policy
        raise KeyError(f"Unknown backup dataset policy: {normalized_dataset_id}")

    def to_manifest(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "strategy_version": self.strategy_version,
            "created_at": self.created_at,
            "dataset_policies": [
                {
                    "dataset_id": policy.dataset_id,
                    "source_path": policy.source_path,
                    "cadence_minutes": policy.cadence_minutes,
                    "retention_points": policy.retention_points,
                    "encryption_required": policy.encryption_required,
                    "integrity_algorithm": policy.integrity_algorithm,
                    "max_snapshot_bytes": policy.max_snapshot_bytes,
                    "metadata": dict(policy.metadata),
                }
                for policy in sorted(self.dataset_policies, key=lambda item: item.dataset_id)
            ],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class BackupSnapshot:
    dataset_id: str
    snapshot_id: str
    content_digest: str
    size_bytes: int
    created_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class BackupExecutionRecord:
    execution_id: str
    profile_id: str
    strategy_version: str
    started_at: str
    completed_at: str
    status: BackupStatus
    failure_reason: str | None
    snapshots: tuple[BackupSnapshot, ...]
    deterministic_digest: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "profile_id": self.profile_id,
            "strategy_version": self.strategy_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "snapshots": [
                {
                    "dataset_id": snapshot.dataset_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "content_digest": snapshot.content_digest,
                    "size_bytes": snapshot.size_bytes,
                    "created_at": snapshot.created_at,
                    "metadata": dict(snapshot.metadata),
                }
                for snapshot in sorted(self.snapshots, key=lambda item: item.dataset_id)
            ],
            "deterministic_digest": self.deterministic_digest,
            "metadata": dict(self.metadata),
        }


class BackupStrategyError(ValueError):
    """Raised when backup strategy inputs violate reliability constraints."""


class BackupStrategyManager:
    """Executes strategy-based backups with deterministic integrity digests."""

    def __init__(self, profile: BackupStrategyProfile | None = None) -> None:
        if profile is None:
            profile = build_default_backup_strategy_profile()
        validate_backup_strategy_profile(profile)
        self.profile = profile

    def run_backup(
        self,
        payloads: dict[str, bytes | str],
        *,
        metadata: dict[str, Any] | None = None,
        fail_on_size: bool = True,
    ) -> BackupExecutionRecord:
        started_at = _utc_now_iso()
        normalized_payloads = _normalize_payloads(payloads)

        missing_payloads = sorted(
            policy.dataset_id
            for policy in self.profile.dataset_policies
            if policy.dataset_id not in normalized_payloads
        )
        if missing_payloads:
            raise BackupStrategyError(
                "Missing backup payloads for datasets: " + ", ".join(missing_payloads)
            )

        snapshots: list[BackupSnapshot] = []
        failure_reason: str | None = None

        for policy in sorted(self.profile.dataset_policies, key=lambda item: item.dataset_id):
            payload = normalized_payloads[policy.dataset_id]
            payload_size = len(payload)

            if payload_size > policy.max_snapshot_bytes:
                if fail_on_size:
                    failure_reason = (
                        f"Dataset {policy.dataset_id} payload size {payload_size} exceeds max_snapshot_bytes "
                        f"{policy.max_snapshot_bytes}"
                    )
                    break
                payload = payload[: policy.max_snapshot_bytes]
                payload_size = len(payload)

            digest = _digest_payload(payload, algorithm=policy.integrity_algorithm)
            snapshot_id = _build_snapshot_id(
                profile_id=self.profile.profile_id,
                dataset_id=policy.dataset_id,
                digest=digest,
                strategy_version=self.profile.strategy_version,
            )
            snapshots.append(
                BackupSnapshot(
                    dataset_id=policy.dataset_id,
                    snapshot_id=snapshot_id,
                    content_digest=digest,
                    size_bytes=payload_size,
                    created_at=_utc_now_iso(),
                    metadata={
                        "source_path": policy.source_path,
                        "encryption_required": policy.encryption_required,
                        "integrity_algorithm": policy.integrity_algorithm,
                        "cadence_minutes": policy.cadence_minutes,
                        "retention_points": policy.retention_points,
                    },
                )
            )

        status: BackupStatus = "failed" if failure_reason else "completed"
        completed_at = _utc_now_iso()
        deterministic_digest = _build_execution_digest(
            profile=self.profile,
            snapshots=snapshots,
            status=status,
            failure_reason=failure_reason,
        )
        execution_id = f"backup-{deterministic_digest[:20]}"

        return BackupExecutionRecord(
            execution_id=execution_id,
            profile_id=self.profile.profile_id,
            strategy_version=self.profile.strategy_version,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            failure_reason=failure_reason,
            snapshots=tuple(sorted(snapshots, key=lambda item: item.dataset_id)),
            deterministic_digest=deterministic_digest,
            metadata=dict(metadata or {}),
        )


def build_default_backup_strategy_profile() -> BackupStrategyProfile:
    policies = (
        BackupDatasetPolicy(
            dataset_id="state",
            source_path=".planning/STATE.md",
            cadence_minutes=60,
            retention_points=168,
            encryption_required=True,
            integrity_algorithm="sha256",
            max_snapshot_bytes=1_000_000,
            metadata={"tier": "critical", "phase": "P11-T4"},
        ),
        BackupDatasetPolicy(
            dataset_id="memory",
            source_path="runtime/memory/",
            cadence_minutes=120,
            retention_points=84,
            encryption_required=True,
            integrity_algorithm="sha256",
            max_snapshot_bytes=5_000_000,
            metadata={"tier": "high", "phase": "P11-T4"},
        ),
        BackupDatasetPolicy(
            dataset_id="configuration",
            source_path="runtime/**/config",
            cadence_minutes=240,
            retention_points=56,
            encryption_required=True,
            integrity_algorithm="sha256",
            max_snapshot_bytes=2_000_000,
            metadata={"tier": "high", "phase": "P11-T4"},
        ),
    )

    profile = BackupStrategyProfile(
        profile_id="friday-core-backup-strategy",
        strategy_version="1.0.0",
        created_at=_utc_now_iso(),
        dataset_policies=tuple(sorted(policies, key=lambda item: item.dataset_id)),
        metadata={
            "program": "production_reliability",
            "phase": "P11-T4",
            "notes": "Baseline backup strategy for launch-critical state, memory, and configuration assets.",
        },
    )
    validate_backup_strategy_profile(profile)
    return profile


def validate_backup_strategy_profile(profile: BackupStrategyProfile) -> None:
    if not isinstance(profile, BackupStrategyProfile):
        raise TypeError("profile must be BackupStrategyProfile")

    _normalize_required(profile.profile_id, "profile_id")
    _normalize_required(profile.strategy_version, "strategy_version")
    _parse_iso(profile.created_at)

    if not profile.dataset_policies:
        raise BackupStrategyError("profile must include at least one dataset policy")

    dataset_ids: set[str] = set()
    for policy in profile.dataset_policies:
        dataset_id = _normalize_required(policy.dataset_id, "dataset_id").lower()
        if dataset_id in dataset_ids:
            raise BackupStrategyError(f"Duplicate dataset policy: {dataset_id}")
        dataset_ids.add(dataset_id)

        _normalize_required(policy.source_path, f"{dataset_id}.source_path")

        if not isinstance(policy.cadence_minutes, int):
            raise TypeError(f"{dataset_id}.cadence_minutes must be an integer")
        if policy.cadence_minutes < 5:
            raise BackupStrategyError(f"{dataset_id}.cadence_minutes must be at least 5")

        if not isinstance(policy.retention_points, int):
            raise TypeError(f"{dataset_id}.retention_points must be an integer")
        if policy.retention_points < 2:
            raise BackupStrategyError(f"{dataset_id}.retention_points must be at least 2")

        if not isinstance(policy.encryption_required, bool):
            raise TypeError(f"{dataset_id}.encryption_required must be boolean")

        algorithm = _normalize_required(policy.integrity_algorithm, f"{dataset_id}.integrity_algorithm").lower()
        if algorithm not in _ALLOWED_INTEGRITY_ALGORITHMS:
            allowed = ", ".join(sorted(_ALLOWED_INTEGRITY_ALGORITHMS))
            raise BackupStrategyError(
                f"Unsupported integrity_algorithm for {dataset_id}: {algorithm}. Allowed: {allowed}"
            )

        if not isinstance(policy.max_snapshot_bytes, int):
            raise TypeError(f"{dataset_id}.max_snapshot_bytes must be an integer")
        if policy.max_snapshot_bytes < 1024:
            raise BackupStrategyError(
                f"{dataset_id}.max_snapshot_bytes must be at least 1024"
            )

    missing_dataset_ids = sorted(_REQUIRED_DATASET_IDS - dataset_ids)
    if missing_dataset_ids:
        raise BackupStrategyError(
            "profile missing required dataset policies: " + ", ".join(missing_dataset_ids)
        )


def _normalize_payloads(payloads: dict[str, bytes | str]) -> dict[str, bytes]:
    if not isinstance(payloads, dict):
        raise TypeError("payloads must be a dict of dataset_id to bytes or str")

    normalized: dict[str, bytes] = {}
    for dataset_id, value in payloads.items():
        normalized_dataset_id = _normalize_required(dataset_id, "payload.dataset_id").lower()
        if isinstance(value, bytes):
            normalized_value = value
        elif isinstance(value, str):
            normalized_value = value.encode("utf-8")
        else:
            raise TypeError(
                f"payload value for dataset {normalized_dataset_id} must be bytes or str"
            )

        normalized[normalized_dataset_id] = normalized_value

    return normalized


def _digest_payload(payload: bytes, *, algorithm: str) -> str:
    if algorithm == "sha256":
        return sha256(payload).hexdigest()
    raise BackupStrategyError(f"Unsupported integrity algorithm: {algorithm}")


def _build_snapshot_id(
    *,
    profile_id: str,
    dataset_id: str,
    digest: str,
    strategy_version: str,
) -> str:
    canonical = json.dumps(
        {
            "profile_id": profile_id,
            "dataset_id": dataset_id,
            "digest": digest,
            "strategy_version": strategy_version,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"snap-{sha256(canonical.encode('utf-8')).hexdigest()[:20]}"


def _build_execution_digest(
    *,
    profile: BackupStrategyProfile,
    snapshots: list[BackupSnapshot],
    status: BackupStatus,
    failure_reason: str | None,
) -> str:
    canonical = json.dumps(
        {
            "profile_id": profile.profile_id,
            "strategy_version": profile.strategy_version,
            "status": status,
            "failure_reason": failure_reason,
            "snapshots": [
                {
                    "dataset_id": snapshot.dataset_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "content_digest": snapshot.content_digest,
                    "size_bytes": snapshot.size_bytes,
                }
                for snapshot in sorted(snapshots, key=lambda item: item.dataset_id)
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
        raise BackupStrategyError(f"{field_name} is required")
    return normalized


def _parse_iso(value: str) -> datetime:
    normalized = _normalize_required(value, "timestamp")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "BackupStatus",
    "BackupDatasetPolicy",
    "BackupStrategyProfile",
    "BackupSnapshot",
    "BackupExecutionRecord",
    "BackupStrategyError",
    "BackupStrategyManager",
    "build_default_backup_strategy_profile",
    "validate_backup_strategy_profile",
]