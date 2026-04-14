"""Tests for P4-T10 connector health checks and retry policies."""

from __future__ import annotations

import unittest
from collections import defaultdict
from typing import Any

from runtime.control_plane import (
    ConnectorHealthMonitor,
    ConnectorManager,
    HostInventoryService,
    HostRecord,
    RetryPolicy,
)


class FlakyAdapter:
    def __init__(self) -> None:
        self.failures_remaining: dict[str, int] = defaultdict(int)
        self.non_retryable_hosts: set[str] = set()
        self.calls: list[dict[str, Any]] = []

    def execute(
        self,
        *,
        host: HostRecord,
        operation: str,
        payload: dict[str, Any],
        identity: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "host_id": host.host_id,
                "operation": operation,
                "payload": dict(payload),
                "identity": identity,
            }
        )

        if host.host_id in self.non_retryable_hosts:
            raise ValueError("invalid_command")

        remaining = self.failures_remaining[host.host_id]
        if remaining > 0:
            self.failures_remaining[host.host_id] = remaining - 1
            raise RuntimeError("connection_unavailable")

        return {
            "status": "ok",
            "host": host.hostname,
            "operation": operation,
        }


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


class ConnectorHealthMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = HostInventoryService()
        self.host_a = self.inventory.register_host(
            hostname="app-1",
            address="10.0.0.10",
            role="app",
            trust_level="high",
        )
        self.host_b = self.inventory.register_host(
            hostname="app-2",
            address="10.0.0.11",
            role="app",
            trust_level="high",
        )

        self.adapter = FlakyAdapter()
        self.sleeps: list[float] = []
        self.monitor = ConnectorHealthMonitor(
            connector_manager=self._build_manager(self.adapter),
            retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.01, backoff_multiplier=2.0),
            sleeper=self.sleeps.append,
        )

    def test_healthy_when_first_attempt_succeeds(self) -> None:
        result = self.monitor.check_host(self.host_a.host_id)

        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(result.errors), 0)
        self.assertIsNotNone(result.last_success_at)

    def test_degraded_when_retry_recovers(self) -> None:
        self.adapter.failures_remaining[self.host_a.host_id] = 1

        result = self.monitor.check_host(self.host_a.host_id)

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(self.sleeps, [0.01])

    def test_unhealthy_when_retries_exhausted(self) -> None:
        self.adapter.failures_remaining[self.host_a.host_id] = 5

        result = self.monitor.check_host(self.host_a.host_id)

        self.assertEqual(result.status, "unhealthy")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(len(result.errors), 3)
        self.assertEqual(self.sleeps, [0.01, 0.02])

    def test_non_retryable_error_does_not_retry(self) -> None:
        self.adapter.non_retryable_hosts.add(self.host_a.host_id)

        result = self.monitor.check_host(self.host_a.host_id)

        self.assertEqual(result.status, "unhealthy")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(self.sleeps, [])

    def test_check_hosts_summary_counts(self) -> None:
        self.adapter.failures_remaining[self.host_a.host_id] = 1
        self.adapter.failures_remaining[self.host_b.host_id] = 5

        summary = self.monitor.check_hosts([self.host_a.host_id, self.host_b.host_id])

        self.assertEqual(summary.total_hosts, 2)
        self.assertEqual(summary.healthy, 0)
        self.assertEqual(summary.degraded, 1)
        self.assertEqual(summary.unhealthy, 1)
        self.assertEqual(len(summary.results), 2)

    def test_retry_policy_validation(self) -> None:
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=0)
        with self.assertRaises(ValueError):
            RetryPolicy(base_delay_seconds=-0.1)
        with self.assertRaises(ValueError):
            RetryPolicy(backoff_multiplier=0.5)

    def _build_manager(self, adapter: FlakyAdapter) -> ConnectorManager:
        manager = ConnectorManager(self.inventory)
        manager.register_adapter(
            name="local-shell",
            adapter=LocalAdapter(),
            transport="local",
            make_default=True,
        )
        manager.register_adapter(
            name="remote-ssh",
            adapter=adapter,
            transport="remote",
            make_default=True,
        )
        return manager


if __name__ == "__main__":
    unittest.main()
