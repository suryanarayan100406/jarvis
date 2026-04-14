"""Connector health checks with retry and backoff policies."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .connector_manager import ConnectorManager


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.1
    backoff_multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be non-negative")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be at least 1")


@dataclass(frozen=True)
class HostHealthResult:
    host_id: str
    status: str
    attempts: int
    latency_ms: float
    errors: tuple[str, ...]
    last_success_at: str | None


@dataclass(frozen=True)
class ConnectorHealthSummary:
    checked_at: str
    total_hosts: int
    healthy: int
    degraded: int
    unhealthy: int
    results: tuple[HostHealthResult, ...]


class ConnectorHealthError(ValueError):
    """Raised when connector health checks encounter invalid input."""


class ConnectorHealthMonitor:
    """Runs connector health checks with bounded retry and exponential backoff."""

    def __init__(
        self,
        connector_manager: ConnectorManager,
        *,
        retry_policy: RetryPolicy | None = None,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.connector_manager = connector_manager
        self.retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleeper or time.sleep
        self._clock = clock or time.perf_counter

    def check_host(
        self,
        host_id: str,
        *,
        operation: str = "collect_status",
        payload: dict[str, Any] | None = None,
        adapter_name: str | None = None,
        identity: str | None = None,
    ) -> HostHealthResult:
        normalized_host_id = _normalize_required(host_id, "host_id")
        normalized_operation = _normalize_required(operation, "operation")
        normalized_payload = dict(payload or {})

        errors: list[str] = []
        attempts = 0
        latency_ms = 0.0
        last_success_at: str | None = None

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            attempts = attempt
            start = self._clock()
            try:
                self.connector_manager.execute(
                    normalized_host_id,
                    normalized_operation,
                    payload=normalized_payload,
                    adapter_name=adapter_name,
                    identity=identity,
                )
                latency_ms = (self._clock() - start) * 1000.0
                last_success_at = _utc_now_iso()
                status = "healthy" if attempt == 1 else "degraded"
                return HostHealthResult(
                    host_id=normalized_host_id,
                    status=status,
                    attempts=attempts,
                    latency_ms=latency_ms,
                    errors=tuple(errors),
                    last_success_at=last_success_at,
                )
            except Exception as exc:
                latency_ms = (self._clock() - start) * 1000.0
                errors.append(f"{type(exc).__name__}: {exc}")

                can_retry = attempt < self.retry_policy.max_attempts and self._is_retryable_error(exc)
                if not can_retry:
                    break

                delay = self._backoff_delay(attempt)
                if delay > 0:
                    self._sleep(delay)

        return HostHealthResult(
            host_id=normalized_host_id,
            status="unhealthy",
            attempts=attempts,
            latency_ms=latency_ms,
            errors=tuple(errors),
            last_success_at=last_success_at,
        )

    def check_hosts(
        self,
        host_ids: list[str] | tuple[str, ...],
        *,
        operation: str = "collect_status",
        payload: dict[str, Any] | None = None,
        adapter_name: str | None = None,
        identity: str | None = None,
    ) -> ConnectorHealthSummary:
        results = [
            self.check_host(
                host_id,
                operation=operation,
                payload=payload,
                adapter_name=adapter_name,
                identity=identity,
            )
            for host_id in host_ids
        ]

        healthy = sum(1 for result in results if result.status == "healthy")
        degraded = sum(1 for result in results if result.status == "degraded")
        unhealthy = sum(1 for result in results if result.status == "unhealthy")

        return ConnectorHealthSummary(
            checked_at=_utc_now_iso(),
            total_hosts=len(results),
            healthy=healthy,
            degraded=degraded,
            unhealthy=unhealthy,
            results=tuple(results),
        )

    def _backoff_delay(self, attempt: int) -> float:
        return self.retry_policy.base_delay_seconds * (
            self.retry_policy.backoff_multiplier ** (attempt - 1)
        )

    @staticmethod
    def _is_retryable_error(error: Exception) -> bool:
        if isinstance(error, (ConnectionError, TimeoutError, RuntimeError)):
            return True

        message = str(error).lower()
        retryable_markers = (
            "timeout",
            "timed out",
            "connection",
            "temporarily unavailable",
            "transient",
        )
        return any(marker in message for marker in retryable_markers)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ConnectorHealthError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
