"""Disaster recovery runbook orchestration with target recovery windows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

RecoveryStepStatus = Literal["completed", "failed", "skipped"]
DrillStatus = Literal["completed", "failed", "degraded"]

_REQUIRED_SUBSYSTEM_IDS = {"orchestration", "memory", "configuration"}


@dataclass(frozen=True)
class RecoveryWindowTarget:
    sequence: int
    step_id: str
    subsystem_id: str
    description: str
    target_rto_minutes: float
    target_rpo_minutes: float
    required: bool
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DisasterRecoveryRunbook:
    runbook_id: str
    runbook_version: str
    created_at: str
    recovery_windows: tuple[RecoveryWindowTarget, ...]
    metadata: dict[str, Any]

    def get_target(self, step_id: str) -> RecoveryWindowTarget:
        normalized_step_id = _normalize_required(step_id, "step_id").lower()
        for target in self.recovery_windows:
            if target.step_id == normalized_step_id:
                return target
        raise KeyError(f"Unknown recovery window target: {normalized_step_id}")

    def to_manifest(self) -> dict[str, Any]:
        return {
            "runbook_id": self.runbook_id,
            "runbook_version": self.runbook_version,
            "created_at": self.created_at,
            "recovery_windows": [
                {
                    "sequence": target.sequence,
                    "step_id": target.step_id,
                    "subsystem_id": target.subsystem_id,
                    "description": target.description,
                    "target_rto_minutes": target.target_rto_minutes,
                    "target_rpo_minutes": target.target_rpo_minutes,
                    "required": target.required,
                    "metadata": dict(target.metadata),
                }
                for target in sorted(self.recovery_windows, key=_sort_targets)
            ],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RecoveryStepObservation:
    step_id: str
    actual_duration_minutes: float
    observed_data_loss_minutes: float
    status: RecoveryStepStatus
    details: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RecoveryWindowEvaluation:
    sequence: int
    step_id: str
    subsystem_id: str
    status: RecoveryStepStatus
    target_rto_minutes: float
    actual_duration_minutes: float
    rto_met: bool
    target_rpo_minutes: float
    observed_data_loss_minutes: float
    rpo_met: bool
    reason: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DisasterRecoveryDrillResult:
    drill_id: str
    runbook_id: str
    runbook_version: str
    started_at: str
    completed_at: str
    status: DrillStatus
    total_steps: int
    successful_steps: int
    failed_steps: int
    skipped_steps: int
    windows_met_count: int
    windows_breached_count: int
    deterministic_digest: str
    evaluations: tuple[RecoveryWindowEvaluation, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "drill_id": self.drill_id,
            "runbook_id": self.runbook_id,
            "runbook_version": self.runbook_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "total_steps": self.total_steps,
            "successful_steps": self.successful_steps,
            "failed_steps": self.failed_steps,
            "skipped_steps": self.skipped_steps,
            "windows_met_count": self.windows_met_count,
            "windows_breached_count": self.windows_breached_count,
            "deterministic_digest": self.deterministic_digest,
            "evaluations": [
                {
                    "sequence": evaluation.sequence,
                    "step_id": evaluation.step_id,
                    "subsystem_id": evaluation.subsystem_id,
                    "status": evaluation.status,
                    "target_rto_minutes": evaluation.target_rto_minutes,
                    "actual_duration_minutes": evaluation.actual_duration_minutes,
                    "rto_met": evaluation.rto_met,
                    "target_rpo_minutes": evaluation.target_rpo_minutes,
                    "observed_data_loss_minutes": evaluation.observed_data_loss_minutes,
                    "rpo_met": evaluation.rpo_met,
                    "reason": evaluation.reason,
                    "metadata": dict(evaluation.metadata),
                }
                for evaluation in sorted(self.evaluations, key=_sort_evaluations)
            ],
            "metadata": dict(self.metadata),
        }


class DisasterRecoveryRunbookError(ValueError):
    """Raised when disaster recovery runbook data is invalid."""


class DisasterRecoveryRunbookManager:
    """Evaluates disaster recovery drills against target recovery windows."""

    def evaluate_drill(
        self,
        runbook: DisasterRecoveryRunbook,
        observations: list[RecoveryStepObservation] | tuple[RecoveryStepObservation, ...],
        *,
        strict: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> DisasterRecoveryDrillResult:
        validate_disaster_recovery_runbook(runbook)
        normalized_observations = _normalize_observations(observations)

        started_at = _utc_now_iso()
        ordered_targets = sorted(runbook.recovery_windows, key=_sort_targets)
        evaluations: list[RecoveryWindowEvaluation] = []

        successful_steps = 0
        failed_steps = 0
        skipped_steps = 0
        windows_met_count = 0
        windows_breached_count = 0
        required_failure = False

        for target in ordered_targets:
            observation = normalized_observations.get(target.step_id)
            if observation is None:
                evaluation = _build_missing_observation_evaluation(target)
            else:
                evaluation = self.evaluate_window(target, observation)

            evaluations.append(evaluation)

            if evaluation.status == "skipped":
                skipped_steps += 1
                continue

            if evaluation.status == "completed" and evaluation.rto_met and evaluation.rpo_met:
                successful_steps += 1
                windows_met_count += 1
                continue

            failed_steps += 1
            windows_breached_count += 1
            if target.required:
                required_failure = True
                if strict:
                    break

        if strict and required_failure and len(evaluations) < len(ordered_targets):
            completed_steps = {evaluation.step_id for evaluation in evaluations}
            for target in ordered_targets:
                if target.step_id in completed_steps:
                    continue
                evaluations.append(_build_skipped_after_failure_evaluation(target))
                skipped_steps += 1

        status: DrillStatus
        if required_failure:
            status = "failed"
        elif windows_breached_count > 0:
            status = "degraded"
        else:
            status = "completed"

        deterministic_digest = _build_drill_digest(
            runbook_id=runbook.runbook_id,
            runbook_version=runbook.runbook_version,
            status=status,
            evaluations=evaluations,
        )

        return DisasterRecoveryDrillResult(
            drill_id=f"drill-{deterministic_digest[:20]}",
            runbook_id=runbook.runbook_id,
            runbook_version=runbook.runbook_version,
            started_at=started_at,
            completed_at=_utc_now_iso(),
            status=status,
            total_steps=len(ordered_targets),
            successful_steps=successful_steps,
            failed_steps=failed_steps,
            skipped_steps=skipped_steps,
            windows_met_count=windows_met_count,
            windows_breached_count=windows_breached_count,
            deterministic_digest=deterministic_digest,
            evaluations=tuple(sorted(evaluations, key=_sort_evaluations)),
            metadata=dict(metadata or {}),
        )

    def evaluate_window(
        self,
        target: RecoveryWindowTarget,
        observation: RecoveryStepObservation,
    ) -> RecoveryWindowEvaluation:
        if not isinstance(target, RecoveryWindowTarget):
            raise TypeError("target must be RecoveryWindowTarget")
        if not isinstance(observation, RecoveryStepObservation):
            raise TypeError("observation must be RecoveryStepObservation")
        if observation.step_id != target.step_id:
            raise DisasterRecoveryRunbookError(
                f"observation step_id {observation.step_id} does not match target {target.step_id}"
            )

        if observation.status == "skipped":
            return RecoveryWindowEvaluation(
                sequence=target.sequence,
                step_id=target.step_id,
                subsystem_id=target.subsystem_id,
                status="skipped",
                target_rto_minutes=target.target_rto_minutes,
                actual_duration_minutes=observation.actual_duration_minutes,
                rto_met=not target.required,
                target_rpo_minutes=target.target_rpo_minutes,
                observed_data_loss_minutes=observation.observed_data_loss_minutes,
                rpo_met=not target.required,
                reason=observation.details or "Step skipped during drill",
                metadata=dict(observation.metadata),
            )

        if observation.status == "failed":
            return RecoveryWindowEvaluation(
                sequence=target.sequence,
                step_id=target.step_id,
                subsystem_id=target.subsystem_id,
                status="failed",
                target_rto_minutes=target.target_rto_minutes,
                actual_duration_minutes=observation.actual_duration_minutes,
                rto_met=False,
                target_rpo_minutes=target.target_rpo_minutes,
                observed_data_loss_minutes=observation.observed_data_loss_minutes,
                rpo_met=False,
                reason=observation.details or "Step reported failed",
                metadata=dict(observation.metadata),
            )

        rto_met = observation.actual_duration_minutes <= target.target_rto_minutes
        rpo_met = observation.observed_data_loss_minutes <= target.target_rpo_minutes

        reasons: list[str] = []
        if not rto_met:
            reasons.append(
                f"RTO window breached: target={target.target_rto_minutes} actual={observation.actual_duration_minutes}"
            )
        if not rpo_met:
            reasons.append(
                f"RPO window breached: target={target.target_rpo_minutes} actual={observation.observed_data_loss_minutes}"
            )

        status: RecoveryStepStatus = "completed" if rto_met and rpo_met else "failed"

        return RecoveryWindowEvaluation(
            sequence=target.sequence,
            step_id=target.step_id,
            subsystem_id=target.subsystem_id,
            status=status,
            target_rto_minutes=target.target_rto_minutes,
            actual_duration_minutes=observation.actual_duration_minutes,
            rto_met=rto_met,
            target_rpo_minutes=target.target_rpo_minutes,
            observed_data_loss_minutes=observation.observed_data_loss_minutes,
            rpo_met=rpo_met,
            reason="; ".join(reasons) if reasons else None,
            metadata=dict(observation.metadata),
        )


def build_default_disaster_recovery_runbook() -> DisasterRecoveryRunbook:
    runbook = DisasterRecoveryRunbook(
        runbook_id="friday-disaster-recovery-runbook",
        runbook_version="1.0.0",
        created_at=_utc_now_iso(),
        recovery_windows=(
            RecoveryWindowTarget(
                sequence=1,
                step_id="orchestration_failover",
                subsystem_id="orchestration",
                description="Fail over orchestration control plane to standby node.",
                target_rto_minutes=15.0,
                target_rpo_minutes=5.0,
                required=True,
                metadata={"phase": "P11-T6", "tier": "critical"},
            ),
            RecoveryWindowTarget(
                sequence=2,
                step_id="memory_restore",
                subsystem_id="memory",
                description="Restore memory index and session state from latest validated backup.",
                target_rto_minutes=30.0,
                target_rpo_minutes=10.0,
                required=True,
                metadata={"phase": "P11-T6", "tier": "critical"},
            ),
            RecoveryWindowTarget(
                sequence=3,
                step_id="configuration_restore",
                subsystem_id="configuration",
                description="Rehydrate configuration store and verify policy baselines.",
                target_rto_minutes=20.0,
                target_rpo_minutes=5.0,
                required=True,
                metadata={"phase": "P11-T6", "tier": "critical"},
            ),
            RecoveryWindowTarget(
                sequence=4,
                step_id="security_verification",
                subsystem_id="security",
                description="Validate security guardrail readiness before reopening production traffic.",
                target_rto_minutes=10.0,
                target_rpo_minutes=0.0,
                required=False,
                metadata={"phase": "P11-T6", "tier": "high"},
            ),
        ),
        metadata={
            "program": "production_reliability",
            "phase": "P11-T6",
            "notes": "Target recovery windows for disaster-recovery drills and launch readiness checks.",
        },
    )
    validate_disaster_recovery_runbook(runbook)
    return runbook


def validate_disaster_recovery_runbook(runbook: DisasterRecoveryRunbook) -> None:
    if not isinstance(runbook, DisasterRecoveryRunbook):
        raise TypeError("runbook must be DisasterRecoveryRunbook")

    _normalize_required(runbook.runbook_id, "runbook_id")
    _normalize_required(runbook.runbook_version, "runbook_version")
    _parse_iso(runbook.created_at)

    if not runbook.recovery_windows:
        raise DisasterRecoveryRunbookError("runbook must include at least one recovery window")

    step_ids: set[str] = set()
    subsystem_ids: set[str] = set()
    sequence_values: set[int] = set()

    for target in runbook.recovery_windows:
        if not isinstance(target, RecoveryWindowTarget):
            raise TypeError("runbook.recovery_windows must contain RecoveryWindowTarget values")

        if not isinstance(target.sequence, int):
            raise TypeError(f"{target.step_id}.sequence must be an integer")
        if target.sequence < 1:
            raise DisasterRecoveryRunbookError(f"{target.step_id}.sequence must be at least 1")
        if target.sequence in sequence_values:
            raise DisasterRecoveryRunbookError(f"Duplicate sequence value: {target.sequence}")
        sequence_values.add(target.sequence)

        step_id = _normalize_required(target.step_id, "step_id").lower()
        if step_id in step_ids:
            raise DisasterRecoveryRunbookError(f"Duplicate recovery step_id: {step_id}")
        step_ids.add(step_id)

        subsystem_id = _normalize_required(target.subsystem_id, f"{step_id}.subsystem_id").lower()
        subsystem_ids.add(subsystem_id)

        _normalize_required(target.description, f"{step_id}.description")

        if not isinstance(target.target_rto_minutes, (int, float)):
            raise TypeError(f"{step_id}.target_rto_minutes must be numeric")
        if target.target_rto_minutes <= 0:
            raise DisasterRecoveryRunbookError(f"{step_id}.target_rto_minutes must be greater than zero")

        if not isinstance(target.target_rpo_minutes, (int, float)):
            raise TypeError(f"{step_id}.target_rpo_minutes must be numeric")
        if target.target_rpo_minutes < 0:
            raise DisasterRecoveryRunbookError(f"{step_id}.target_rpo_minutes cannot be negative")

        if not isinstance(target.required, bool):
            raise TypeError(f"{step_id}.required must be boolean")

    missing_subsystems = sorted(_REQUIRED_SUBSYSTEM_IDS - subsystem_ids)
    if missing_subsystems:
        raise DisasterRecoveryRunbookError(
            "runbook missing required subsystem recovery windows: " + ", ".join(missing_subsystems)
        )


def _normalize_observations(
    observations: list[RecoveryStepObservation] | tuple[RecoveryStepObservation, ...],
) -> dict[str, RecoveryStepObservation]:
    if not isinstance(observations, (list, tuple)):
        raise TypeError("observations must be list or tuple of RecoveryStepObservation")

    normalized: dict[str, RecoveryStepObservation] = {}
    for observation in observations:
        if not isinstance(observation, RecoveryStepObservation):
            raise TypeError("observations must contain RecoveryStepObservation values")

        step_id = _normalize_required(observation.step_id, "observation.step_id").lower()
        if step_id in normalized:
            raise DisasterRecoveryRunbookError(f"Duplicate observation for step_id {step_id}")

        if not isinstance(observation.actual_duration_minutes, (int, float)):
            raise TypeError(f"{step_id}.actual_duration_minutes must be numeric")
        if observation.actual_duration_minutes < 0:
            raise DisasterRecoveryRunbookError(f"{step_id}.actual_duration_minutes cannot be negative")

        if not isinstance(observation.observed_data_loss_minutes, (int, float)):
            raise TypeError(f"{step_id}.observed_data_loss_minutes must be numeric")
        if observation.observed_data_loss_minutes < 0:
            raise DisasterRecoveryRunbookError(
                f"{step_id}.observed_data_loss_minutes cannot be negative"
            )

        normalized[step_id] = RecoveryStepObservation(
            step_id=step_id,
            actual_duration_minutes=float(observation.actual_duration_minutes),
            observed_data_loss_minutes=float(observation.observed_data_loss_minutes),
            status=observation.status,
            details=observation.details,
            metadata=dict(observation.metadata),
        )

    return normalized


def _build_missing_observation_evaluation(target: RecoveryWindowTarget) -> RecoveryWindowEvaluation:
    if target.required:
        return RecoveryWindowEvaluation(
            sequence=target.sequence,
            step_id=target.step_id,
            subsystem_id=target.subsystem_id,
            status="failed",
            target_rto_minutes=target.target_rto_minutes,
            actual_duration_minutes=0.0,
            rto_met=False,
            target_rpo_minutes=target.target_rpo_minutes,
            observed_data_loss_minutes=0.0,
            rpo_met=False,
            reason="Missing observation for required recovery step",
            metadata={},
        )

    return RecoveryWindowEvaluation(
        sequence=target.sequence,
        step_id=target.step_id,
        subsystem_id=target.subsystem_id,
        status="skipped",
        target_rto_minutes=target.target_rto_minutes,
        actual_duration_minutes=0.0,
        rto_met=True,
        target_rpo_minutes=target.target_rpo_minutes,
        observed_data_loss_minutes=0.0,
        rpo_met=True,
        reason="No observation provided for optional recovery step",
        metadata={},
    )


def _build_skipped_after_failure_evaluation(target: RecoveryWindowTarget) -> RecoveryWindowEvaluation:
    return RecoveryWindowEvaluation(
        sequence=target.sequence,
        step_id=target.step_id,
        subsystem_id=target.subsystem_id,
        status="skipped",
        target_rto_minutes=target.target_rto_minutes,
        actual_duration_minutes=0.0,
        rto_met=not target.required,
        target_rpo_minutes=target.target_rpo_minutes,
        observed_data_loss_minutes=0.0,
        rpo_met=not target.required,
        reason="Skipped due to strict failure in earlier recovery step",
        metadata={},
    )


def _build_drill_digest(
    *,
    runbook_id: str,
    runbook_version: str,
    status: DrillStatus,
    evaluations: list[RecoveryWindowEvaluation],
) -> str:
    canonical = json.dumps(
        {
            "runbook_id": runbook_id,
            "runbook_version": runbook_version,
            "status": status,
            "evaluations": [
                {
                    "sequence": evaluation.sequence,
                    "step_id": evaluation.step_id,
                    "subsystem_id": evaluation.subsystem_id,
                    "status": evaluation.status,
                    "target_rto_minutes": evaluation.target_rto_minutes,
                    "actual_duration_minutes": evaluation.actual_duration_minutes,
                    "rto_met": evaluation.rto_met,
                    "target_rpo_minutes": evaluation.target_rpo_minutes,
                    "observed_data_loss_minutes": evaluation.observed_data_loss_minutes,
                    "rpo_met": evaluation.rpo_met,
                    "reason": evaluation.reason,
                }
                for evaluation in sorted(evaluations, key=_sort_evaluations)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _sort_targets(target: RecoveryWindowTarget) -> tuple[int, str]:
    return (target.sequence, target.step_id)


def _sort_evaluations(evaluation: RecoveryWindowEvaluation) -> tuple[int, str]:
    return (evaluation.sequence, evaluation.step_id)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise DisasterRecoveryRunbookError(f"{field_name} is required")
    return normalized


def _parse_iso(value: str) -> datetime:
    normalized = _normalize_required(value, "created_at")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise DisasterRecoveryRunbookError(f"Invalid ISO-8601 datetime: {value}") from exc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "RecoveryStepStatus",
    "DrillStatus",
    "RecoveryWindowTarget",
    "DisasterRecoveryRunbook",
    "RecoveryStepObservation",
    "RecoveryWindowEvaluation",
    "DisasterRecoveryDrillResult",
    "DisasterRecoveryRunbookError",
    "DisasterRecoveryRunbookManager",
    "build_default_disaster_recovery_runbook",
    "validate_disaster_recovery_runbook",
]
