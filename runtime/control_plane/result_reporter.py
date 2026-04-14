"""Structured result aggregation and host-by-host reporting for control-plane operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .parallel_orchestrator import HostOperationResult, ParallelExecutionResult


@dataclass(frozen=True)
class HostReportEntry:
    host_id: str
    operation: str
    status: str
    adapter_name: str | None
    transport: str | None
    identity: str | None
    error: str | None
    result: dict[str, Any] | None


@dataclass(frozen=True)
class AggregatedControlPlaneReport:
    report_id: str
    orchestration_id: str
    created_at: str
    started_at: str
    finished_at: str
    duration_seconds: float
    total_requests: int
    succeeded: int
    failed: int
    skipped: int
    by_status: dict[str, int]
    hosts: tuple[HostReportEntry, ...]
    failures: tuple[HostReportEntry, ...]


class ResultReporterError(ValueError):
    """Raised when result aggregation inputs are invalid."""


class ControlPlaneResultReporter:
    """Aggregates orchestration outcomes into structured and human-readable host reports."""

    def aggregate(self, execution: ParallelExecutionResult) -> AggregatedControlPlaneReport:
        if execution.total_requests < 0:
            raise ResultReporterError("execution total_requests must be non-negative")

        host_entries = [self._to_host_entry(result) for result in execution.results]
        host_entries.sort(key=lambda entry: (entry.host_id, entry.operation, entry.status))

        failures = tuple(entry for entry in host_entries if entry.status == "error")
        by_status = {
            "success": sum(1 for entry in host_entries if entry.status == "success"),
            "error": sum(1 for entry in host_entries if entry.status == "error"),
            "skipped": sum(1 for entry in host_entries if entry.status == "skipped"),
        }

        started = _parse_iso(execution.started_at)
        finished = _parse_iso(execution.finished_at)
        duration_seconds = max(0.0, (finished - started).total_seconds())

        return AggregatedControlPlaneReport(
            report_id=str(uuid4()),
            orchestration_id=execution.orchestration_id,
            created_at=_utc_now_iso(),
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            duration_seconds=duration_seconds,
            total_requests=execution.total_requests,
            succeeded=execution.succeeded,
            failed=execution.failed,
            skipped=execution.skipped,
            by_status=by_status,
            hosts=tuple(host_entries),
            failures=failures,
        )

    def as_dict(self, report: AggregatedControlPlaneReport) -> dict[str, Any]:
        return {
            "report_id": report.report_id,
            "orchestration_id": report.orchestration_id,
            "created_at": report.created_at,
            "started_at": report.started_at,
            "finished_at": report.finished_at,
            "duration_seconds": report.duration_seconds,
            "summary": {
                "total_requests": report.total_requests,
                "succeeded": report.succeeded,
                "failed": report.failed,
                "skipped": report.skipped,
                "by_status": dict(report.by_status),
            },
            "hosts": [self._entry_to_dict(entry) for entry in report.hosts],
            "failures": [self._entry_to_dict(entry) for entry in report.failures],
        }

    def render_text(self, report: AggregatedControlPlaneReport) -> str:
        lines = [
            "[CONTROL-PLANE REPORT]",
            f"Orchestration: {report.orchestration_id}",
            (
                "Summary: "
                f"total={report.total_requests}, success={report.succeeded}, "
                f"failed={report.failed}, skipped={report.skipped}, duration={report.duration_seconds:.3f}s"
            ),
            "Hosts:",
        ]

        for entry in report.hosts:
            line = f"- {entry.host_id} | {entry.operation} | {entry.status}"
            if entry.adapter_name:
                line = f"{line} | adapter={entry.adapter_name}"
            if entry.transport:
                line = f"{line} | transport={entry.transport}"
            if entry.error:
                line = f"{line} | error={entry.error}"
            lines.append(line)

        if report.failures:
            lines.append("Failures:")
            for entry in report.failures:
                lines.append(f"- {entry.host_id} | {entry.operation} | {entry.error}")

        return "\n".join(lines)

    @staticmethod
    def _to_host_entry(result: HostOperationResult) -> HostReportEntry:
        adapter_name: str | None = None
        transport: str | None = None
        identity: str | None = None
        payload: dict[str, Any] | None = None

        if isinstance(result.result, dict):
            adapter_name = _normalize_optional(result.result.get("adapter_name"))
            transport = _normalize_optional(result.result.get("transport"))
            identity = _normalize_optional(result.result.get("identity"))
            payload_value = result.result.get("result")
            if isinstance(payload_value, dict):
                payload = dict(payload_value)

        return HostReportEntry(
            host_id=result.host_id,
            operation=result.operation,
            status=result.status,
            adapter_name=adapter_name,
            transport=transport,
            identity=identity,
            error=result.error,
            result=payload,
        )

    @staticmethod
    def _entry_to_dict(entry: HostReportEntry) -> dict[str, Any]:
        return {
            "host_id": entry.host_id,
            "operation": entry.operation,
            "status": entry.status,
            "adapter_name": entry.adapter_name,
            "transport": entry.transport,
            "identity": entry.identity,
            "error": entry.error,
            "result": dict(entry.result) if entry.result is not None else None,
        }


def _parse_iso(timestamp: str) -> datetime:
    normalized = timestamp
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)


def _normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
