"""Tests for P4-T8 bounded parallel host orchestration."""

from __future__ import annotations

import threading
import time
import unittest
from typing import Any

from runtime.control_plane import (
    ConnectorManager,
    HostInventoryService,
    HostOperationRequest,
    HostRecord,
    ParallelHostOrchestrator,
    ParallelOrchestratorError,
)


class TrackingAdapter:
    def __init__(self, *, delay_seconds: float = 0.0, fail_host_ids: set[str] | None = None) -> None:
        self.delay_seconds = delay_seconds
        self.fail_host_ids = set(fail_host_ids or set())
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0

    def execute(
        self,
        *,
        host: HostRecord,
        operation: str,
        payload: dict[str, Any],
        identity: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._active += 1
            if self._active > self.max_active:
                self.max_active = self._active

        try:
            self.calls.append(
                {
                    "host_id": host.host_id,
                    "operation": operation,
                    "payload": dict(payload),
                    "identity": identity,
                }
            )
            if self.delay_seconds > 0:
                time.sleep(self.delay_seconds)

            if host.host_id in self.fail_host_ids:
                raise RuntimeError(f"forced_failure:{host.host_id}")

            return {
                "status": "ok",
                "host": host.hostname,
                "operation": operation,
            }
        finally:
            with self._lock:
                self._active -= 1


class LocalAdapter:
    def execute(
        self,
        *,
        host: HostRecord,
        operation: str,
        payload: dict[str, Any],
        identity: str | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "host": host.hostname,
            "operation": operation,
        }


class ParallelHostOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = HostInventoryService()
        self.hosts = [
            self.inventory.register_host(
                hostname=f"app-{index}",
                address=f"10.0.0.{index}",
                role="app",
                trust_level="high",
            )
            for index in range(1, 6)
        ]
        self.local_host = self.inventory.register_host(
            hostname="laptop-main",
            address="127.0.0.1",
            role="local",
            trust_level="high",
        )

    def test_executes_requests_across_hosts(self) -> None:
        remote_adapter = TrackingAdapter()
        orchestrator = self._build_orchestrator(remote_adapter, max_concurrency=3)
        requests = self._requests(self.hosts[:3], operation="collect_status")

        result = orchestrator.execute_requests(requests)

        self.assertEqual(result.total_requests, 3)
        self.assertEqual(result.succeeded, 3)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(len(remote_adapter.calls), 3)

    def test_respects_configured_max_concurrency(self) -> None:
        remote_adapter = TrackingAdapter(delay_seconds=0.05)
        orchestrator = self._build_orchestrator(remote_adapter, max_concurrency=2)
        requests = self._requests(self.hosts, operation="collect_status")

        result = orchestrator.execute_requests(requests)

        self.assertEqual(result.succeeded, len(requests))
        self.assertLessEqual(result.observed_max_concurrency, 2)
        self.assertLessEqual(remote_adapter.max_active, 2)

    def test_stop_on_error_skips_unscheduled_requests(self) -> None:
        failing_host = self.hosts[0]
        remote_adapter = TrackingAdapter(fail_host_ids={failing_host.host_id})
        orchestrator = self._build_orchestrator(remote_adapter, max_concurrency=1)
        requests = self._requests(self.hosts[:3], operation="collect_status")

        result = orchestrator.execute_requests(requests, stop_on_error=True)

        self.assertEqual(result.failed, 1)
        self.assertEqual(result.skipped, 2)
        self.assertEqual(len(remote_adapter.calls), 1)

    def test_without_stop_on_error_collects_partial_failures(self) -> None:
        failing_host = self.hosts[1]
        remote_adapter = TrackingAdapter(fail_host_ids={failing_host.host_id})
        orchestrator = self._build_orchestrator(remote_adapter, max_concurrency=2)
        requests = self._requests(self.hosts[:3], operation="collect_status")

        result = orchestrator.execute_requests(requests, stop_on_error=False)

        self.assertEqual(result.total_requests, 3)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.succeeded, 2)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(len(remote_adapter.calls), 3)

    def test_invalid_max_concurrency_is_rejected(self) -> None:
        manager = ConnectorManager(self.inventory)
        with self.assertRaises(ParallelOrchestratorError):
            ParallelHostOrchestrator(manager, max_concurrency=0)

    def _build_orchestrator(self, remote_adapter: TrackingAdapter, *, max_concurrency: int) -> ParallelHostOrchestrator:
        manager = ConnectorManager(self.inventory)
        manager.register_adapter(
            name="local-shell",
            adapter=LocalAdapter(),
            transport="local",
            make_default=True,
        )
        manager.register_adapter(
            name="remote-ssh",
            adapter=remote_adapter,
            transport="remote",
            make_default=True,
        )
        return ParallelHostOrchestrator(manager, max_concurrency=max_concurrency)

    @staticmethod
    def _requests(hosts: list[HostRecord], *, operation: str) -> list[HostOperationRequest]:
        return [
            HostOperationRequest(
                host_id=host.host_id,
                operation=operation,
                payload={"command": "uptime"},
            )
            for host in hosts
        ]


if __name__ == "__main__":
    unittest.main()
