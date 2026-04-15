"""Release pipeline orchestration with canary evaluation and rollback support."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

CanaryStatus = Literal["passed", "failed"]
PipelineStatus = Literal["pending_canary", "canary_failed", "promoted", "rolled_back"]
RollbackState = Literal["not_available", "available", "executed"]


@dataclass(frozen=True)
class CanaryThresholdPolicy:
    policy_id: str
    policy_version: str
    max_error_rate: float
    min_success_rate: float
    max_p95_latency_ms: float
    min_requests: int
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "max_error_rate": self.max_error_rate,
            "min_success_rate": self.min_success_rate,
            "max_p95_latency_ms": self.max_p95_latency_ms,
            "min_requests": self.min_requests,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CanaryObservation:
    request_count: int
    error_count: int
    p95_latency_ms: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CanaryEvaluation:
    status: CanaryStatus
    observed_error_rate: float
    observed_success_rate: float
    observed_p95_latency_ms: float
    threshold_error_rate: float
    threshold_success_rate: float
    threshold_p95_latency_ms: float
    minimum_requests: int
    reasons: tuple[str, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "observed_error_rate": self.observed_error_rate,
            "observed_success_rate": self.observed_success_rate,
            "observed_p95_latency_ms": self.observed_p95_latency_ms,
            "threshold_error_rate": self.threshold_error_rate,
            "threshold_success_rate": self.threshold_success_rate,
            "threshold_p95_latency_ms": self.threshold_p95_latency_ms,
            "minimum_requests": self.minimum_requests,
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ReleasePipelineRecord:
    pipeline_id: str
    release_id: str
    previous_release_id: str
    canary_percentage: int
    created_at: str
    status: PipelineStatus
    policy_id: str
    policy_version: str
    canary_evaluation: CanaryEvaluation | None
    rollback_state: RollbackState
    rollback_token: str | None
    promoted_at: str | None
    rolled_back_at: str | None
    rollback_reason: str | None
    deterministic_digest: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "release_id": self.release_id,
            "previous_release_id": self.previous_release_id,
            "canary_percentage": self.canary_percentage,
            "created_at": self.created_at,
            "status": self.status,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "canary_evaluation": (
                self.canary_evaluation.to_manifest() if self.canary_evaluation is not None else None
            ),
            "rollback_state": self.rollback_state,
            "rollback_token": self.rollback_token,
            "promoted_at": self.promoted_at,
            "rolled_back_at": self.rolled_back_at,
            "rollback_reason": self.rollback_reason,
            "deterministic_digest": self.deterministic_digest,
            "metadata": dict(self.metadata),
        }


class ReleasePipelineError(ValueError):
    """Raised when release pipeline operations violate rollout policy."""


class ReleasePipelineManager:
    """Manages canary-gated release promotion and rollback lifecycle."""

    def __init__(self, policy: CanaryThresholdPolicy | None = None) -> None:
        if policy is None:
            policy = build_default_canary_threshold_policy()
        validate_canary_threshold_policy(policy)

        self.policy = policy
        self._pipelines: dict[str, ReleasePipelineRecord] = {}
        self._consumed_rollback_tokens: set[str] = set()

    def create_pipeline(
        self,
        *,
        release_id: str,
        previous_release_id: str,
        canary_percentage: int = 10,
        metadata: dict[str, Any] | None = None,
    ) -> ReleasePipelineRecord:
        normalized_release_id = _normalize_required(release_id, "release_id").lower()
        normalized_previous_release_id = _normalize_required(
            previous_release_id,
            "previous_release_id",
        ).lower()

        if normalized_release_id == normalized_previous_release_id:
            raise ReleasePipelineError("release_id must differ from previous_release_id")

        if not isinstance(canary_percentage, int):
            raise TypeError("canary_percentage must be an integer")
        if canary_percentage < 1 or canary_percentage > 50:
            raise ReleasePipelineError("canary_percentage must be between 1 and 50")

        pipeline_id = f"pipeline-{len(self._pipelines) + 1:04d}-{normalized_release_id}"
        record = ReleasePipelineRecord(
            pipeline_id=pipeline_id,
            release_id=normalized_release_id,
            previous_release_id=normalized_previous_release_id,
            canary_percentage=canary_percentage,
            created_at=_utc_now_iso(),
            status="pending_canary",
            policy_id=self.policy.policy_id,
            policy_version=self.policy.policy_version,
            canary_evaluation=None,
            rollback_state="not_available",
            rollback_token=None,
            promoted_at=None,
            rolled_back_at=None,
            rollback_reason=None,
            deterministic_digest=_build_pipeline_digest(
                pipeline_id=pipeline_id,
                status="pending_canary",
                release_id=normalized_release_id,
                previous_release_id=normalized_previous_release_id,
                canary_percentage=canary_percentage,
                policy=self.policy,
                canary_evaluation=None,
                rollback_state="not_available",
                rollback_reason=None,
            ),
            metadata=dict(metadata or {}),
        )

        self._pipelines[record.pipeline_id] = record
        return record

    def evaluate_canary(
        self,
        pipeline_id: str,
        observation: CanaryObservation,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ReleasePipelineRecord:
        pipeline = self.get_pipeline(pipeline_id)
        if pipeline.status != "pending_canary":
            raise ReleasePipelineError(
                f"Pipeline {pipeline.pipeline_id} is {pipeline.status}; canary can be evaluated only once"
            )

        evaluation = self.evaluate_observation(observation)
        if evaluation.status == "passed":
            status: PipelineStatus = "promoted"
            promoted_at = _utc_now_iso()
            rollback_state: RollbackState = "available"
            rollback_reason = None
        else:
            status = "canary_failed"
            promoted_at = None
            rollback_state = "available"
            rollback_reason = "Canary threshold policy failed"

        rollback_token = _build_rollback_token(
            pipeline_id=pipeline.pipeline_id,
            release_id=pipeline.release_id,
            previous_release_id=pipeline.previous_release_id,
        )

        merged_metadata = dict(pipeline.metadata)
        merged_metadata.update(metadata or {})

        updated = replace(
            pipeline,
            status=status,
            canary_evaluation=evaluation,
            rollback_state=rollback_state,
            rollback_token=rollback_token,
            promoted_at=promoted_at,
            rollback_reason=rollback_reason,
            deterministic_digest=_build_pipeline_digest(
                pipeline_id=pipeline.pipeline_id,
                status=status,
                release_id=pipeline.release_id,
                previous_release_id=pipeline.previous_release_id,
                canary_percentage=pipeline.canary_percentage,
                policy=self.policy,
                canary_evaluation=evaluation,
                rollback_state=rollback_state,
                rollback_reason=rollback_reason,
            ),
            metadata=merged_metadata,
        )
        self._pipelines[pipeline.pipeline_id] = updated
        return updated

    def execute_rollback(
        self,
        *,
        pipeline_id: str,
        rollback_token: str,
        reason: str,
        actor_role: str,
        metadata: dict[str, Any] | None = None,
    ) -> ReleasePipelineRecord:
        pipeline = self.get_pipeline(pipeline_id)
        if pipeline.status not in {"promoted", "canary_failed"}:
            raise ReleasePipelineError(
                f"Pipeline {pipeline.pipeline_id} is {pipeline.status}; rollback is not allowed"
            )

        if pipeline.rollback_state != "available":
            raise ReleasePipelineError(
                f"Pipeline {pipeline.pipeline_id} rollback state is {pipeline.rollback_state}"
            )

        normalized_token = _normalize_required(rollback_token, "rollback_token")
        if pipeline.rollback_token is None or normalized_token != pipeline.rollback_token:
            raise ReleasePipelineError("rollback_token does not match pipeline record")
        if normalized_token in self._consumed_rollback_tokens:
            raise ReleasePipelineError("rollback_token has already been consumed")

        normalized_reason = _normalize_required(reason, "reason")
        normalized_actor_role = _normalize_required(actor_role, "actor_role").lower()

        self._consumed_rollback_tokens.add(normalized_token)

        merged_metadata = dict(pipeline.metadata)
        merged_metadata.update(metadata or {})
        merged_metadata["rollback_actor_role"] = normalized_actor_role

        updated = replace(
            pipeline,
            status="rolled_back",
            rollback_state="executed",
            rolled_back_at=_utc_now_iso(),
            rollback_reason=normalized_reason,
            deterministic_digest=_build_pipeline_digest(
                pipeline_id=pipeline.pipeline_id,
                status="rolled_back",
                release_id=pipeline.release_id,
                previous_release_id=pipeline.previous_release_id,
                canary_percentage=pipeline.canary_percentage,
                policy=self.policy,
                canary_evaluation=pipeline.canary_evaluation,
                rollback_state="executed",
                rollback_reason=normalized_reason,
            ),
            metadata=merged_metadata,
        )

        self._pipelines[pipeline.pipeline_id] = updated
        return updated

    def evaluate_observation(self, observation: CanaryObservation) -> CanaryEvaluation:
        if not isinstance(observation, CanaryObservation):
            raise TypeError("observation must be CanaryObservation")

        if not isinstance(observation.request_count, int):
            raise TypeError("request_count must be an integer")
        if observation.request_count < 1:
            raise ReleasePipelineError("request_count must be at least 1")

        if not isinstance(observation.error_count, int):
            raise TypeError("error_count must be an integer")
        if observation.error_count < 0:
            raise ReleasePipelineError("error_count cannot be negative")
        if observation.error_count > observation.request_count:
            raise ReleasePipelineError("error_count cannot exceed request_count")

        if not isinstance(observation.p95_latency_ms, (int, float)):
            raise TypeError("p95_latency_ms must be numeric")
        if observation.p95_latency_ms < 0:
            raise ReleasePipelineError("p95_latency_ms cannot be negative")

        observed_error_rate = round(observation.error_count / observation.request_count, 12)
        observed_success_rate = round(1.0 - observed_error_rate, 12)

        reasons: list[str] = []
        if observation.request_count < self.policy.min_requests:
            reasons.append(
                f"Canary sample too small: minimum={self.policy.min_requests} actual={observation.request_count}"
            )
        if observed_error_rate > self.policy.max_error_rate:
            reasons.append(
                f"Error rate breached: max={self.policy.max_error_rate} actual={observed_error_rate}"
            )
        if observed_success_rate < self.policy.min_success_rate:
            reasons.append(
                f"Success rate breached: min={self.policy.min_success_rate} actual={observed_success_rate}"
            )
        if observation.p95_latency_ms > self.policy.max_p95_latency_ms:
            reasons.append(
                "Latency breached: "
                f"max={self.policy.max_p95_latency_ms} actual={float(observation.p95_latency_ms)}"
            )

        status: CanaryStatus = "passed" if not reasons else "failed"

        return CanaryEvaluation(
            status=status,
            observed_error_rate=observed_error_rate,
            observed_success_rate=observed_success_rate,
            observed_p95_latency_ms=float(observation.p95_latency_ms),
            threshold_error_rate=self.policy.max_error_rate,
            threshold_success_rate=self.policy.min_success_rate,
            threshold_p95_latency_ms=self.policy.max_p95_latency_ms,
            minimum_requests=self.policy.min_requests,
            reasons=tuple(reasons),
            metadata={
                "request_count": observation.request_count,
                "error_count": observation.error_count,
                **dict(observation.metadata),
            },
        )

    def get_pipeline(self, pipeline_id: str) -> ReleasePipelineRecord:
        normalized_pipeline_id = _normalize_required(pipeline_id, "pipeline_id")
        pipeline = self._pipelines.get(normalized_pipeline_id)
        if pipeline is None:
            raise KeyError(f"Unknown release pipeline: {normalized_pipeline_id}")
        return pipeline

    def list_pipelines(self, *, status: PipelineStatus | None = None) -> list[ReleasePipelineRecord]:
        pipelines = list(self._pipelines.values())
        if status is not None:
            normalized_status = _normalize_required(status, "status").lower()
            pipelines = [item for item in pipelines if item.status == normalized_status]

        return sorted(pipelines, key=lambda item: item.pipeline_id)


def build_default_canary_threshold_policy() -> CanaryThresholdPolicy:
    policy = CanaryThresholdPolicy(
        policy_id="friday-release-canary-policy",
        policy_version="1.0.0",
        max_error_rate=0.02,
        min_success_rate=0.98,
        max_p95_latency_ms=250.0,
        min_requests=500,
        metadata={
            "program": "production_reliability",
            "phase": "P11-T7",
            "notes": "Default canary thresholds for launch release promotions.",
        },
    )
    validate_canary_threshold_policy(policy)
    return policy


def validate_canary_threshold_policy(policy: CanaryThresholdPolicy) -> None:
    if not isinstance(policy, CanaryThresholdPolicy):
        raise TypeError("policy must be CanaryThresholdPolicy")

    _normalize_required(policy.policy_id, "policy_id")
    _normalize_required(policy.policy_version, "policy_version")

    if not isinstance(policy.max_error_rate, (int, float)):
        raise TypeError("max_error_rate must be numeric")
    if policy.max_error_rate < 0 or policy.max_error_rate > 1:
        raise ReleasePipelineError("max_error_rate must be between 0 and 1")

    if not isinstance(policy.min_success_rate, (int, float)):
        raise TypeError("min_success_rate must be numeric")
    if policy.min_success_rate < 0 or policy.min_success_rate > 1:
        raise ReleasePipelineError("min_success_rate must be between 0 and 1")

    if not isinstance(policy.max_p95_latency_ms, (int, float)):
        raise TypeError("max_p95_latency_ms must be numeric")
    if policy.max_p95_latency_ms <= 0:
        raise ReleasePipelineError("max_p95_latency_ms must be greater than zero")

    if not isinstance(policy.min_requests, int):
        raise TypeError("min_requests must be an integer")
    if policy.min_requests < 1:
        raise ReleasePipelineError("min_requests must be at least 1")


def _build_pipeline_digest(
    *,
    pipeline_id: str,
    status: PipelineStatus,
    release_id: str,
    previous_release_id: str,
    canary_percentage: int,
    policy: CanaryThresholdPolicy,
    canary_evaluation: CanaryEvaluation | None,
    rollback_state: RollbackState,
    rollback_reason: str | None,
) -> str:
    canonical = json.dumps(
        {
            "pipeline_id": pipeline_id,
            "status": status,
            "release_id": release_id,
            "previous_release_id": previous_release_id,
            "canary_percentage": canary_percentage,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "canary_evaluation": (
                canary_evaluation.to_manifest() if canary_evaluation is not None else None
            ),
            "rollback_state": rollback_state,
            "rollback_reason": rollback_reason,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _build_rollback_token(
    *,
    pipeline_id: str,
    release_id: str,
    previous_release_id: str,
) -> str:
    canonical = json.dumps(
        {
            "pipeline_id": pipeline_id,
            "release_id": release_id,
            "previous_release_id": previous_release_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    return f"rollback-{digest[:24]}"


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ReleasePipelineError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "CanaryStatus",
    "PipelineStatus",
    "RollbackState",
    "CanaryThresholdPolicy",
    "CanaryObservation",
    "CanaryEvaluation",
    "ReleasePipelineRecord",
    "ReleasePipelineError",
    "ReleasePipelineManager",
    "build_default_canary_threshold_policy",
    "validate_canary_threshold_policy",
]
