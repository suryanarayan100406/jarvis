"""SLO and error-budget definitions for core production subsystems."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

BurnStatus = Literal["healthy", "warning", "critical", "breached"]

_REQUIRED_SUBSYSTEM_IDS = {
    "orchestration",
    "planner",
    "executor",
    "memory",
    "policy",
    "security",
}


@dataclass(frozen=True)
class SubsystemSLODefinition:
    slo_id: str
    subsystem_id: str
    objective: str
    indicator_id: str
    target_ratio: float
    window_days: int
    error_budget_ratio: float
    warning_burn_rate: float
    critical_burn_rate: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SLOCatalog:
    catalog_id: str
    catalog_version: str
    created_at: str
    slos: tuple[SubsystemSLODefinition, ...]
    metadata: dict[str, Any]

    def get_slo(self, slo_id: str) -> SubsystemSLODefinition:
        normalized_slo_id = _normalize_required(slo_id, "slo_id").lower()
        for slo in self.slos:
            if slo.slo_id == normalized_slo_id:
                return slo
        raise KeyError(f"Unknown SLO definition: {normalized_slo_id}")

    def list_slos(self, *, subsystem_id: str | None = None) -> list[SubsystemSLODefinition]:
        if subsystem_id is None:
            return sorted(self.slos, key=lambda item: item.slo_id)

        normalized_subsystem_id = _normalize_required(subsystem_id, "subsystem_id").lower()
        return sorted(
            [slo for slo in self.slos if slo.subsystem_id == normalized_subsystem_id],
            key=lambda item: item.slo_id,
        )

    def to_manifest(self) -> dict[str, Any]:
        return {
            "catalog_id": self.catalog_id,
            "catalog_version": self.catalog_version,
            "created_at": self.created_at,
            "slos": [
                {
                    "slo_id": slo.slo_id,
                    "subsystem_id": slo.subsystem_id,
                    "objective": slo.objective,
                    "indicator_id": slo.indicator_id,
                    "target_ratio": slo.target_ratio,
                    "window_days": slo.window_days,
                    "error_budget_ratio": slo.error_budget_ratio,
                    "warning_burn_rate": slo.warning_burn_rate,
                    "critical_burn_rate": slo.critical_burn_rate,
                    "metadata": dict(slo.metadata),
                }
                for slo in sorted(self.slos, key=lambda item: item.slo_id)
            ],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SLOObservation:
    slo_id: str
    total_events: int
    compliant_events: int
    elapsed_days: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ErrorBudgetEvaluation:
    slo_id: str
    subsystem_id: str
    target_ratio: float
    achieved_ratio: float
    error_budget_ratio: float
    observed_error_ratio: float
    consumed_budget_ratio: float
    remaining_budget_ratio: float
    burn_rate: float
    elapsed_days: int
    status: BurnStatus
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ErrorBudgetReport:
    report_id: str
    generated_at: str
    catalog_id: str
    catalog_version: str
    evaluation_count: int
    healthy_count: int
    warning_count: int
    critical_count: int
    breached_count: int
    evaluations: tuple[ErrorBudgetEvaluation, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "catalog_id": self.catalog_id,
            "catalog_version": self.catalog_version,
            "evaluation_count": self.evaluation_count,
            "healthy_count": self.healthy_count,
            "warning_count": self.warning_count,
            "critical_count": self.critical_count,
            "breached_count": self.breached_count,
            "evaluations": [
                {
                    "slo_id": evaluation.slo_id,
                    "subsystem_id": evaluation.subsystem_id,
                    "target_ratio": evaluation.target_ratio,
                    "achieved_ratio": evaluation.achieved_ratio,
                    "error_budget_ratio": evaluation.error_budget_ratio,
                    "observed_error_ratio": evaluation.observed_error_ratio,
                    "consumed_budget_ratio": evaluation.consumed_budget_ratio,
                    "remaining_budget_ratio": evaluation.remaining_budget_ratio,
                    "burn_rate": evaluation.burn_rate,
                    "elapsed_days": evaluation.elapsed_days,
                    "status": evaluation.status,
                    "metadata": dict(evaluation.metadata),
                }
                for evaluation in sorted(self.evaluations, key=lambda item: item.slo_id)
            ],
            "metadata": dict(self.metadata),
        }


class SLOErrorBudgetError(ValueError):
    """Raised when SLO catalogs or error-budget evaluations are invalid."""


class ErrorBudgetMonitor:
    """Evaluates SLO observations against defined error budgets."""

    def evaluate_catalog(
        self,
        catalog: SLOCatalog,
        observations: list[SLOObservation] | tuple[SLOObservation, ...],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ErrorBudgetReport:
        validate_slo_catalog(catalog)
        normalized_observations = _normalize_observations(observations)

        observation_by_slo_id: dict[str, SLOObservation] = {}
        for observation in normalized_observations:
            if observation.slo_id in observation_by_slo_id:
                raise SLOErrorBudgetError(
                    f"Duplicate observation for slo_id {observation.slo_id}"
                )
            observation_by_slo_id[observation.slo_id] = observation

        missing_observations = sorted(
            slo.slo_id for slo in catalog.slos if slo.slo_id not in observation_by_slo_id
        )
        if missing_observations:
            raise SLOErrorBudgetError(
                "Missing observations for SLOs: " + ", ".join(missing_observations)
            )

        evaluations: list[ErrorBudgetEvaluation] = []
        for slo in sorted(catalog.slos, key=lambda item: item.slo_id):
            evaluations.append(
                self.evaluate_slo(slo, observation_by_slo_id[slo.slo_id])
            )

        report_id = _build_report_id(catalog=catalog, evaluations=evaluations)
        return ErrorBudgetReport(
            report_id=report_id,
            generated_at=_utc_now_iso(),
            catalog_id=catalog.catalog_id,
            catalog_version=catalog.catalog_version,
            evaluation_count=len(evaluations),
            healthy_count=sum(1 for item in evaluations if item.status == "healthy"),
            warning_count=sum(1 for item in evaluations if item.status == "warning"),
            critical_count=sum(1 for item in evaluations if item.status == "critical"),
            breached_count=sum(1 for item in evaluations if item.status == "breached"),
            evaluations=tuple(evaluations),
            metadata=dict(metadata or {}),
        )

    def evaluate_slo(
        self,
        slo: SubsystemSLODefinition,
        observation: SLOObservation,
    ) -> ErrorBudgetEvaluation:
        if not isinstance(slo, SubsystemSLODefinition):
            raise TypeError("slo must be SubsystemSLODefinition")
        if not isinstance(observation, SLOObservation):
            raise TypeError("observation must be SLOObservation")

        if observation.slo_id != slo.slo_id:
            raise SLOErrorBudgetError(
                f"Observation slo_id {observation.slo_id} does not match definition {slo.slo_id}"
            )

        achieved_ratio = round(observation.compliant_events / observation.total_events, 12)
        observed_error_ratio = round(1.0 - achieved_ratio, 12)

        if slo.error_budget_ratio <= 0:
            consumed_budget_ratio = 0.0 if observed_error_ratio <= 0 else float("inf")
            remaining_budget_ratio = 1.0 if observed_error_ratio <= 0 else 0.0
        else:
            consumed_budget_ratio = round(observed_error_ratio / slo.error_budget_ratio, 12)
            remaining_budget_ratio = round(max(0.0, 1.0 - consumed_budget_ratio), 12)

        expected_budget_use = observation.elapsed_days / slo.window_days
        expected_budget_use = max(1.0 / slo.window_days, min(1.0, expected_budget_use))

        if consumed_budget_ratio == float("inf"):
            burn_rate = float("inf")
        else:
            burn_rate = round(consumed_budget_ratio / expected_budget_use, 12)

        status = _derive_status(
            observed_error_ratio=observed_error_ratio,
            error_budget_ratio=slo.error_budget_ratio,
            burn_rate=burn_rate,
            warning_burn_rate=slo.warning_burn_rate,
            critical_burn_rate=slo.critical_burn_rate,
        )

        return ErrorBudgetEvaluation(
            slo_id=slo.slo_id,
            subsystem_id=slo.subsystem_id,
            target_ratio=slo.target_ratio,
            achieved_ratio=achieved_ratio,
            error_budget_ratio=slo.error_budget_ratio,
            observed_error_ratio=observed_error_ratio,
            consumed_budget_ratio=consumed_budget_ratio,
            remaining_budget_ratio=remaining_budget_ratio,
            burn_rate=burn_rate,
            elapsed_days=observation.elapsed_days,
            status=status,
            metadata={
                "total_events": observation.total_events,
                "compliant_events": observation.compliant_events,
                "window_days": slo.window_days,
                **dict(observation.metadata),
            },
        )


def build_default_core_slo_catalog() -> SLOCatalog:
    slos = (
        SubsystemSLODefinition(
            slo_id="orchestration_cycle_success",
            subsystem_id="orchestration",
            objective="Autonomous cycle success ratio",
            indicator_id="cycle_success_rate",
            target_ratio=0.995,
            window_days=28,
            error_budget_ratio=0.005,
            warning_burn_rate=1.0,
            critical_burn_rate=2.0,
            metadata={"phase": "P11-T1"},
        ),
        SubsystemSLODefinition(
            slo_id="planner_plan_validity",
            subsystem_id="planner",
            objective="Plan validity ratio",
            indicator_id="valid_plan_ratio",
            target_ratio=0.995,
            window_days=28,
            error_budget_ratio=0.005,
            warning_burn_rate=1.0,
            critical_burn_rate=2.0,
            metadata={"phase": "P11-T1"},
        ),
        SubsystemSLODefinition(
            slo_id="executor_run_success",
            subsystem_id="executor",
            objective="Executor run success ratio",
            indicator_id="run_success_ratio",
            target_ratio=0.997,
            window_days=28,
            error_budget_ratio=0.003,
            warning_burn_rate=1.0,
            critical_burn_rate=2.0,
            metadata={"phase": "P11-T1"},
        ),
        SubsystemSLODefinition(
            slo_id="memory_retrieval_grounded",
            subsystem_id="memory",
            objective="Grounded memory retrieval ratio",
            indicator_id="grounded_retrieval_ratio",
            target_ratio=0.992,
            window_days=28,
            error_budget_ratio=0.008,
            warning_burn_rate=1.0,
            critical_burn_rate=2.0,
            metadata={"phase": "P11-T1"},
        ),
        SubsystemSLODefinition(
            slo_id="policy_decision_integrity",
            subsystem_id="policy",
            objective="Policy decision integrity ratio",
            indicator_id="policy_integrity_ratio",
            target_ratio=0.999,
            window_days=28,
            error_budget_ratio=0.001,
            warning_burn_rate=1.0,
            critical_burn_rate=2.0,
            metadata={"phase": "P11-T1"},
        ),
        SubsystemSLODefinition(
            slo_id="security_guardrail_enforcement",
            subsystem_id="security",
            objective="Guardrail enforcement ratio",
            indicator_id="guardrail_enforcement_ratio",
            target_ratio=0.9995,
            window_days=28,
            error_budget_ratio=0.0005,
            warning_burn_rate=1.0,
            critical_burn_rate=2.0,
            metadata={"phase": "P11-T1"},
        ),
    )

    catalog = SLOCatalog(
        catalog_id="friday-core-slos",
        catalog_version="1.0.0",
        created_at=_utc_now_iso(),
        slos=tuple(sorted(slos, key=lambda item: item.slo_id)),
        metadata={
            "program": "production_reliability",
            "phase": "P11-T1",
            "notes": "Core subsystem SLO and error-budget baseline for launch readiness.",
        },
    )
    validate_slo_catalog(catalog)
    return catalog


def validate_slo_catalog(catalog: SLOCatalog) -> None:
    if not isinstance(catalog, SLOCatalog):
        raise TypeError("catalog must be SLOCatalog")

    _normalize_required(catalog.catalog_id, "catalog_id")
    _normalize_required(catalog.catalog_version, "catalog_version")
    _parse_iso(catalog.created_at)

    if not catalog.slos:
        raise SLOErrorBudgetError("catalog must include at least one SLO definition")

    seen_slo_ids: set[str] = set()
    subsystems: set[str] = set()
    for slo in catalog.slos:
        slo_id = _normalize_required(slo.slo_id, "slo_id").lower()
        if slo_id in seen_slo_ids:
            raise SLOErrorBudgetError(f"Duplicate slo_id: {slo_id}")
        seen_slo_ids.add(slo_id)

        subsystem_id = _normalize_required(slo.subsystem_id, "subsystem_id").lower()
        subsystems.add(subsystem_id)

        _normalize_required(slo.objective, f"{slo_id}.objective")
        _normalize_required(slo.indicator_id, f"{slo_id}.indicator_id")
        target_ratio = _normalize_ratio(slo.target_ratio, f"{slo_id}.target_ratio")
        if target_ratio < 0.9:
            raise SLOErrorBudgetError(
                f"{slo_id}.target_ratio must be at least 0.9 for launch SLO baselines"
            )

        if not isinstance(slo.window_days, int):
            raise TypeError(f"{slo_id}.window_days must be an integer")
        if slo.window_days < 7:
            raise SLOErrorBudgetError(f"{slo_id}.window_days must be at least 7")

        error_budget_ratio = _normalize_ratio(slo.error_budget_ratio, f"{slo_id}.error_budget_ratio")
        expected_error_budget = round(1.0 - target_ratio, 12)
        if abs(error_budget_ratio - expected_error_budget) > 1e-9:
            raise SLOErrorBudgetError(
                f"{slo_id}.error_budget_ratio must equal 1-target_ratio ({expected_error_budget:.12f})"
            )

        warning_burn = _normalize_positive(slo.warning_burn_rate, f"{slo_id}.warning_burn_rate")
        critical_burn = _normalize_positive(slo.critical_burn_rate, f"{slo_id}.critical_burn_rate")
        if critical_burn < warning_burn:
            raise SLOErrorBudgetError(
                f"{slo_id}.critical_burn_rate must be >= warning_burn_rate"
            )

    missing_subsystems = sorted(_REQUIRED_SUBSYSTEM_IDS - subsystems)
    if missing_subsystems:
        raise SLOErrorBudgetError(
            "catalog missing required core subsystems: " + ", ".join(missing_subsystems)
        )


def _normalize_observations(
    observations: list[SLOObservation] | tuple[SLOObservation, ...],
) -> tuple[SLOObservation, ...]:
    if not observations:
        raise SLOErrorBudgetError("observations must include at least one entry")

    normalized: list[SLOObservation] = []
    for observation in observations:
        if not isinstance(observation, SLOObservation):
            raise TypeError("observations must contain SLOObservation entries")

        slo_id = _normalize_required(observation.slo_id, "observation.slo_id").lower()
        if not isinstance(observation.total_events, int):
            raise TypeError("total_events must be an integer")
        if not isinstance(observation.compliant_events, int):
            raise TypeError("compliant_events must be an integer")
        if not isinstance(observation.elapsed_days, int):
            raise TypeError("elapsed_days must be an integer")

        if observation.total_events < 1:
            raise SLOErrorBudgetError("total_events must be at least 1")
        if observation.compliant_events < 0:
            raise SLOErrorBudgetError("compliant_events cannot be negative")
        if observation.compliant_events > observation.total_events:
            raise SLOErrorBudgetError("compliant_events cannot exceed total_events")
        if observation.elapsed_days < 1:
            raise SLOErrorBudgetError("elapsed_days must be at least 1")

        normalized.append(
            SLOObservation(
                slo_id=slo_id,
                total_events=observation.total_events,
                compliant_events=observation.compliant_events,
                elapsed_days=observation.elapsed_days,
                metadata=dict(observation.metadata),
            )
        )

    return tuple(sorted(normalized, key=lambda item: item.slo_id))


def _derive_status(
    *,
    observed_error_ratio: float,
    error_budget_ratio: float,
    burn_rate: float,
    warning_burn_rate: float,
    critical_burn_rate: float,
) -> BurnStatus:
    if observed_error_ratio > error_budget_ratio:
        return "breached"
    if burn_rate >= critical_burn_rate:
        return "critical"
    if burn_rate >= warning_burn_rate:
        return "warning"
    return "healthy"


def _build_report_id(
    *,
    catalog: SLOCatalog,
    evaluations: list[ErrorBudgetEvaluation],
) -> str:
    canonical = json.dumps(
        {
            "catalog_id": catalog.catalog_id,
            "catalog_version": catalog.catalog_version,
            "evaluations": [
                {
                    "slo_id": evaluation.slo_id,
                    "status": evaluation.status,
                    "achieved_ratio": evaluation.achieved_ratio,
                    "burn_rate": evaluation.burn_rate,
                }
                for evaluation in sorted(evaluations, key=lambda item: item.slo_id)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"slo-report-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise SLOErrorBudgetError(f"{field_name} is required")
    return normalized


def _normalize_ratio(value: float, field_name: str) -> float:
    try:
        normalized = float(value)
    except Exception as exc:
        raise SLOErrorBudgetError(f"{field_name} must be numeric") from exc
    if normalized < 0 or normalized > 1:
        raise SLOErrorBudgetError(f"{field_name} must be between 0 and 1")
    return round(normalized, 12)


def _normalize_positive(value: float, field_name: str) -> float:
    try:
        normalized = float(value)
    except Exception as exc:
        raise SLOErrorBudgetError(f"{field_name} must be numeric") from exc
    if normalized <= 0:
        raise SLOErrorBudgetError(f"{field_name} must be positive")
    return round(normalized, 12)


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
    "BurnStatus",
    "SubsystemSLODefinition",
    "SLOCatalog",
    "SLOObservation",
    "ErrorBudgetEvaluation",
    "ErrorBudgetReport",
    "SLOErrorBudgetError",
    "ErrorBudgetMonitor",
    "build_default_core_slo_catalog",
    "validate_slo_catalog",
]