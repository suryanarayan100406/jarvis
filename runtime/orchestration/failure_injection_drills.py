"""Failure-injection drills across critical services for launch reliability."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from time import perf_counter
from typing import Any, Callable, Literal

FailureOutcome = Literal["contained", "degraded", "failed"]
FailureSeverity = Literal["high", "critical"]
FailureType = Literal[
    "timeout",
    "dependency_outage",
    "data_corruption",
    "permission_denied",
    "crash_loop",
]

_FAILURE_TYPES = {"timeout", "dependency_outage", "data_corruption", "permission_denied", "crash_loop"}
_FAILURE_SEVERITIES = {"high", "critical"}

FailureInjectionHandler = Callable[["FailureInjectionScenario", dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class FailureInjectionScenario:
    scenario_id: str
    title: str
    service_id: str
    fault_type: FailureType
    severity: FailureSeverity
    target_response_seconds: float
    expected_outcomes: tuple[FailureOutcome, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FailureInjectionResult:
    scenario_id: str
    service_id: str
    status: FailureOutcome
    fault_type: FailureType
    severity: FailureSeverity
    recovered: bool
    rollback_triggered: bool
    detection_seconds: float
    response_seconds: float
    target_response_seconds: float
    reason: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FailureInjectionReport:
    report_id: str
    started_at: str
    completed_at: str
    total_scenarios: int
    contained_count: int
    degraded_count: int
    failed_count: int
    readiness_score: float
    deterministic_digest: str
    summary: str
    results: tuple[FailureInjectionResult, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_scenarios": self.total_scenarios,
            "contained_count": self.contained_count,
            "degraded_count": self.degraded_count,
            "failed_count": self.failed_count,
            "readiness_score": self.readiness_score,
            "deterministic_digest": self.deterministic_digest,
            "summary": self.summary,
            "results": [
                {
                    "scenario_id": result.scenario_id,
                    "service_id": result.service_id,
                    "status": result.status,
                    "fault_type": result.fault_type,
                    "severity": result.severity,
                    "recovered": result.recovered,
                    "rollback_triggered": result.rollback_triggered,
                    "detection_seconds": result.detection_seconds,
                    "response_seconds": result.response_seconds,
                    "target_response_seconds": result.target_response_seconds,
                    "reason": result.reason,
                    "metadata": dict(result.metadata),
                }
                for result in sorted(self.results, key=lambda item: item.scenario_id)
            ],
            "metadata": dict(self.metadata),
        }


class FailureInjectionDrillError(ValueError):
    """Raised when failure-injection drill inputs are invalid."""


class FailureInjectionDrillRunner:
    """Executes critical-service failure drills and grades containment readiness."""

    def __init__(
        self,
        handlers: dict[str, FailureInjectionHandler],
        *,
        timer: Callable[[], float] | None = None,
    ) -> None:
        if timer is None:
            timer = perf_counter
        self._handlers = {k.lower(): v for k, v in dict(handlers).items()}
        self._timer = timer

    def run_drills(
        self,
        scenarios: list[FailureInjectionScenario] | tuple[FailureInjectionScenario, ...],
        *,
        context: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FailureInjectionReport:
        normalized_scenarios = [self._normalize_scenario(item) for item in scenarios]
        if not normalized_scenarios:
            raise FailureInjectionDrillError("At least one failure-injection scenario is required")

        started_at = _utc_now_iso()
        shared_context = dict(context or {})
        results: list[FailureInjectionResult] = []

        for scenario in normalized_scenarios:
            scenario_context = dict(shared_context)
            scenario_context["scenario_id"] = scenario.scenario_id
            scenario_context["service_id"] = scenario.service_id

            start = self._timer()
            handler = self._handlers.get(scenario.service_id)
            if handler is None:
                elapsed = max(0.0, self._timer() - start)
                results.append(
                    FailureInjectionResult(
                        scenario_id=scenario.scenario_id,
                        service_id=scenario.service_id,
                        status="failed",
                        fault_type=scenario.fault_type,
                        severity=scenario.severity,
                        recovered=False,
                        rollback_triggered=False,
                        detection_seconds=elapsed,
                        response_seconds=elapsed,
                        target_response_seconds=scenario.target_response_seconds,
                        reason=f"No handler registered for service_id {scenario.service_id}",
                        metadata=dict(scenario.metadata),
                    )
                )
                continue

            try:
                output = handler(scenario, scenario_context)
            except Exception as exc:  # pragma: no cover - boundary guard
                elapsed = max(0.0, self._timer() - start)
                results.append(
                    FailureInjectionResult(
                        scenario_id=scenario.scenario_id,
                        service_id=scenario.service_id,
                        status="failed",
                        fault_type=scenario.fault_type,
                        severity=scenario.severity,
                        recovered=False,
                        rollback_triggered=False,
                        detection_seconds=elapsed,
                        response_seconds=elapsed,
                        target_response_seconds=scenario.target_response_seconds,
                        reason=f"Handler error: {type(exc).__name__}: {exc}",
                        metadata=dict(scenario.metadata),
                    )
                )
                continue

            elapsed = max(0.0, self._timer() - start)
            results.append(self._evaluate_output(scenario, output, elapsed))

        contained_count = sum(1 for item in results if item.status == "contained")
        degraded_count = sum(1 for item in results if item.status == "degraded")
        failed_count = sum(1 for item in results if item.status == "failed")
        total = len(results)
        readiness_score = round((contained_count + (0.5 * degraded_count)) / total, 4)

        summary = (
            f"Failure drills: contained={contained_count}/{total}, "
            f"degraded={degraded_count}, failed={failed_count}, readiness={readiness_score:.2f}."
        )

        deterministic_digest = _build_report_digest(results)
        return FailureInjectionReport(
            report_id=f"failure-drill-{deterministic_digest[:20]}",
            started_at=started_at,
            completed_at=_utc_now_iso(),
            total_scenarios=total,
            contained_count=contained_count,
            degraded_count=degraded_count,
            failed_count=failed_count,
            readiness_score=readiness_score,
            deterministic_digest=deterministic_digest,
            summary=summary,
            results=tuple(sorted(results, key=lambda item: item.scenario_id)),
            metadata=dict(metadata or {}),
        )

    def _normalize_scenario(self, scenario: FailureInjectionScenario) -> FailureInjectionScenario:
        if not isinstance(scenario, FailureInjectionScenario):
            raise TypeError("scenarios must contain FailureInjectionScenario values")

        scenario_id = _normalize_required(scenario.scenario_id, "scenario_id").lower()
        title = _normalize_required(scenario.title, "title")
        service_id = _normalize_required(scenario.service_id, "service_id").lower()
        fault_type = _normalize_required(scenario.fault_type, "fault_type").lower()
        severity = _normalize_required(scenario.severity, "severity").lower()

        if fault_type not in _FAILURE_TYPES:
            allowed = ", ".join(sorted(_FAILURE_TYPES))
            raise FailureInjectionDrillError(
                f"Unsupported fault_type {fault_type}. Allowed: {allowed}"
            )
        if severity not in _FAILURE_SEVERITIES:
            allowed = ", ".join(sorted(_FAILURE_SEVERITIES))
            raise FailureInjectionDrillError(
                f"Unsupported severity {severity}. Allowed: {allowed}"
            )

        if not isinstance(scenario.target_response_seconds, (int, float)):
            raise TypeError("target_response_seconds must be numeric")
        if scenario.target_response_seconds <= 0:
            raise FailureInjectionDrillError("target_response_seconds must be greater than zero")

        expected_outcomes = tuple(
            sorted({_normalize_required(item, "expected_outcome").lower() for item in scenario.expected_outcomes})
        )
        if not expected_outcomes:
            raise FailureInjectionDrillError("expected_outcomes must include at least one outcome")

        unknown_outcomes = [
            item for item in expected_outcomes if item not in {"contained", "degraded", "failed"}
        ]
        if unknown_outcomes:
            raise FailureInjectionDrillError(
                "Unsupported expected_outcomes: " + ", ".join(unknown_outcomes)
            )

        return FailureInjectionScenario(
            scenario_id=scenario_id,
            title=title,
            service_id=service_id,
            fault_type=fault_type,
            severity=severity,
            target_response_seconds=float(scenario.target_response_seconds),
            expected_outcomes=expected_outcomes,
            metadata=dict(scenario.metadata),
        )

    def _evaluate_output(
        self,
        scenario: FailureInjectionScenario,
        output: dict[str, Any],
        elapsed: float,
    ) -> FailureInjectionResult:
        if not isinstance(output, dict):
            raise TypeError("drill handler output must be a dict")

        raw_status = _normalize_required(output.get("status", "failed"), "status").lower()
        if raw_status not in {"contained", "degraded", "failed"}:
            raw_status = "failed"

        detection_seconds = float(output.get("detection_seconds", elapsed))
        response_seconds = float(output.get("response_seconds", elapsed))
        recovered = bool(output.get("recovered", raw_status != "failed"))
        rollback_triggered = bool(output.get("rollback_triggered", False))
        detail = _normalize_optional(output.get("detail"))

        if detection_seconds < 0 or response_seconds < 0:
            raise FailureInjectionDrillError("detection_seconds and response_seconds cannot be negative")

        within_budget = response_seconds <= scenario.target_response_seconds
        final_status: FailureOutcome

        if raw_status == "failed":
            final_status = "failed"
            reason = detail or "Service failure was not contained"
        elif not within_budget:
            final_status = "degraded"
            reason = (
                detail
                or "Failure contained but response exceeded target response window"
            )
        elif raw_status not in scenario.expected_outcomes:
            final_status = "degraded"
            reason = detail or "Observed status did not match expected drill outcomes"
        else:
            final_status = raw_status
            reason = detail or "Failure response met expected containment behavior"

        merged_metadata = dict(scenario.metadata)
        merged_metadata.update(dict(output.get("metadata", {})))

        return FailureInjectionResult(
            scenario_id=scenario.scenario_id,
            service_id=scenario.service_id,
            status=final_status,
            fault_type=scenario.fault_type,
            severity=scenario.severity,
            recovered=recovered,
            rollback_triggered=rollback_triggered,
            detection_seconds=round(detection_seconds, 6),
            response_seconds=round(response_seconds, 6),
            target_response_seconds=scenario.target_response_seconds,
            reason=reason,
            metadata=merged_metadata,
        )


def default_failure_injection_scenarios() -> tuple[FailureInjectionScenario, ...]:
    return (
        FailureInjectionScenario(
            scenario_id="critical-orchestration-crash-loop",
            title="Orchestration crash loop containment",
            service_id="orchestration",
            fault_type="crash_loop",
            severity="critical",
            target_response_seconds=30.0,
            expected_outcomes=("contained", "degraded"),
            metadata={"phase": "P11-T10"},
        ),
        FailureInjectionScenario(
            scenario_id="critical-memory-corruption",
            title="Memory index corruption recovery",
            service_id="memory",
            fault_type="data_corruption",
            severity="critical",
            target_response_seconds=45.0,
            expected_outcomes=("contained",),
            metadata={"phase": "P11-T10"},
        ),
        FailureInjectionScenario(
            scenario_id="critical-configuration-outage",
            title="Configuration backend outage handling",
            service_id="configuration",
            fault_type="dependency_outage",
            severity="high",
            target_response_seconds=40.0,
            expected_outcomes=("contained", "degraded"),
            metadata={"phase": "P11-T10"},
        ),
        FailureInjectionScenario(
            scenario_id="critical-security-timeout",
            title="Security control timeout response",
            service_id="security",
            fault_type="timeout",
            severity="critical",
            target_response_seconds=20.0,
            expected_outcomes=("contained", "degraded"),
            metadata={"phase": "P11-T10"},
        ),
        FailureInjectionScenario(
            scenario_id="critical-release-pipeline-failure",
            title="Release pipeline failure rollback",
            service_id="release_pipeline",
            fault_type="permission_denied",
            severity="high",
            target_response_seconds=25.0,
            expected_outcomes=("contained",),
            metadata={"phase": "P11-T10"},
        ),
    )


def _build_report_digest(results: list[FailureInjectionResult]) -> str:
    canonical = json.dumps(
        [
            {
                "scenario_id": result.scenario_id,
                "service_id": result.service_id,
                "status": result.status,
                "fault_type": result.fault_type,
                "severity": result.severity,
                "recovered": result.recovered,
                "rollback_triggered": result.rollback_triggered,
                "detection_seconds": result.detection_seconds,
                "response_seconds": result.response_seconds,
                "target_response_seconds": result.target_response_seconds,
                "reason": result.reason,
            }
            for result in sorted(results, key=lambda item: item.scenario_id)
        ],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise FailureInjectionDrillError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "FailureOutcome",
    "FailureSeverity",
    "FailureType",
    "FailureInjectionScenario",
    "FailureInjectionResult",
    "FailureInjectionReport",
    "FailureInjectionDrillError",
    "FailureInjectionDrillRunner",
    "default_failure_injection_scenarios",
]
