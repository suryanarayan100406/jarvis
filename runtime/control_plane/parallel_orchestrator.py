"""Parallel host orchestration with bounded concurrency controls."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from .connector_manager import ConnectorManager

ExecutionStatus = Literal["success", "error", "skipped"]


@dataclass(frozen=True)
class HostOperationRequest:
    host_id: str
    operation: str
    payload: dict[str, Any] | None = None
    adapter_name: str | None = None
    identity: str | None = None


@dataclass(frozen=True)
class HostOperationResult:
    host_id: str
    operation: str
    status: ExecutionStatus
    result: dict[str, Any] | None
    error: str | None


@dataclass(frozen=True)
class ParallelExecutionResult:
    orchestration_id: str
    total_requests: int
    succeeded: int
    failed: int
    skipped: int
    configured_max_concurrency: int
    observed_max_concurrency: int
    started_at: str
    finished_at: str
    results: tuple[HostOperationResult, ...]


class ParallelOrchestratorError(ValueError):
    """Raised when orchestration configuration or execution is invalid."""


class ParallelHostOrchestrator:
    """Executes host operations in parallel with a strict concurrency upper bound."""

    def __init__(self, connector_manager: ConnectorManager, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ParallelOrchestratorError("max_concurrency must be at least 1")

        self.connector_manager = connector_manager
        self.max_concurrency = max_concurrency
        self._active_workers = 0
        self._observed_max_concurrency = 0
        self._active_lock = Lock()

    def execute_requests(
        self,
        requests: list[HostOperationRequest] | tuple[HostOperationRequest, ...],
        *,
        stop_on_error: bool = False,
        timeout_seconds: float | None = None,
    ) -> ParallelExecutionResult:
        normalized_requests = [self._normalize_request(request) for request in requests]
        started_at = _utc_now_iso()
        self._reset_observed_concurrency()

        if not normalized_requests:
            return ParallelExecutionResult(
                orchestration_id=str(uuid4()),
                total_requests=0,
                succeeded=0,
                failed=0,
                skipped=0,
                configured_max_concurrency=self.max_concurrency,
                observed_max_concurrency=0,
                started_at=started_at,
                finished_at=_utc_now_iso(),
                results=(),
            )

        orchestration_id = str(uuid4())
        completed: list[HostOperationResult] = []
        pending_iter = iter(normalized_requests)
        unscheduled: list[HostOperationRequest] = []
        stop_scheduling = False

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            in_flight: dict[Future[HostOperationResult], HostOperationRequest] = {}

            while True:
                while not stop_scheduling and len(in_flight) < self.max_concurrency:
                    try:
                        request = next(pending_iter)
                    except StopIteration:
                        break
                    future = executor.submit(self._execute_request, request)
                    in_flight[future] = request

                if not in_flight:
                    if stop_scheduling:
                        unscheduled.extend(list(pending_iter))
                    break

                done, _ = wait(
                    set(in_flight.keys()),
                    timeout=timeout_seconds,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    raise ParallelOrchestratorError(
                        f"Parallel orchestration timed out after {timeout_seconds} seconds"
                    )

                for future in done:
                    request = in_flight.pop(future)
                    result = future.result()
                    completed.append(result)

                    if stop_on_error and result.status == "error":
                        stop_scheduling = True

            if stop_scheduling:
                for request in unscheduled:
                    completed.append(
                        HostOperationResult(
                            host_id=request.host_id,
                            operation=request.operation,
                            status="skipped",
                            result=None,
                            error="Skipped due to stop_on_error after previous failure",
                        )
                    )

        completed.sort(key=lambda item: (item.host_id, item.operation, item.status))

        succeeded = sum(1 for result in completed if result.status == "success")
        failed = sum(1 for result in completed if result.status == "error")
        skipped = sum(1 for result in completed if result.status == "skipped")

        return ParallelExecutionResult(
            orchestration_id=orchestration_id,
            total_requests=len(normalized_requests),
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            configured_max_concurrency=self.max_concurrency,
            observed_max_concurrency=self._observed_max_concurrency,
            started_at=started_at,
            finished_at=_utc_now_iso(),
            results=tuple(completed),
        )

    def _execute_request(self, request: HostOperationRequest) -> HostOperationResult:
        self._enter_worker()
        try:
            result = self.connector_manager.execute(
                request.host_id,
                request.operation,
                payload=request.payload,
                adapter_name=request.adapter_name,
                identity=request.identity,
            )
            return HostOperationResult(
                host_id=request.host_id,
                operation=request.operation,
                status="success",
                result={
                    "adapter_name": result.adapter_name,
                    "transport": result.transport,
                    "identity": result.identity,
                    "payload": dict(result.payload),
                    "result": dict(result.result),
                },
                error=None,
            )
        except Exception as exc:
            return HostOperationResult(
                host_id=request.host_id,
                operation=request.operation,
                status="error",
                result=None,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            self._exit_worker()

    def _normalize_request(self, request: HostOperationRequest) -> HostOperationRequest:
        normalized_host_id = _normalize_required(request.host_id, "host_id")
        normalized_operation = _normalize_required(request.operation, "operation")
        normalized_payload = dict(request.payload or {})
        normalized_adapter_name = _normalize_optional(request.adapter_name)
        normalized_identity = _normalize_optional(request.identity)

        return HostOperationRequest(
            host_id=normalized_host_id,
            operation=normalized_operation,
            payload=normalized_payload,
            adapter_name=normalized_adapter_name,
            identity=normalized_identity,
        )

    def _reset_observed_concurrency(self) -> None:
        with self._active_lock:
            self._active_workers = 0
            self._observed_max_concurrency = 0

    def _enter_worker(self) -> None:
        with self._active_lock:
            self._active_workers += 1
            if self._active_workers > self._observed_max_concurrency:
                self._observed_max_concurrency = self._active_workers

    def _exit_worker(self) -> None:
        with self._active_lock:
            self._active_workers -= 1


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ParallelOrchestratorError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
