"""Integration tests for P4-T11 multi-host control-plane workflows."""

from __future__ import annotations

import unittest
from collections import defaultdict
from typing import Any

from runtime.control_plane import (
    ConnectorManager,
    ControlPlaneResultReporter,
    DryRunExecutionGate,
    DryRunGateError,
    HostInventoryService,
    HostOperationRequest,
    HostRecord,
    ParallelHostOrchestrator,
    RollbackActionManager,
)


class IntegrationRemoteAdapter:
    def __init__(self) -> None:
        self.failures_remaining: dict[tuple[str, str], int] = defaultdict(int)
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
                "hostname": host.hostname,
                "operation": operation,
                "payload": dict(payload),
                "identity": identity,
            }
        )

        key = (host.host_id, operation)
        remaining = self.failures_remaining[key]
        if remaining > 0:
            self.failures_remaining[key] = remaining - 1
            raise RuntimeError(f"forced_failure:{host.hostname}:{operation}")

        return {
            "status": "ok",
            "host": host.hostname,
            "operation": operation,
        }


class IntegrationLocalAdapter:
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


class ControlPlaneMultiHostIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = HostInventoryService()
        self.local_host = self.inventory.register_host(
            hostname="laptop-main",
            address="127.0.0.1",
            role="local",
            trust_level="high",
        )
        self.app_1 = self.inventory.register_host(
            hostname="app-1",
            address="10.0.0.10",
            role="app",
            trust_level="high",
        )
        self.app_2 = self.inventory.register_host(
            hostname="app-2",
            address="10.0.0.11",
            role="app",
            trust_level="high",
        )
        self.db_1 = self.inventory.register_host(
            hostname="db-1",
            address="10.0.0.12",
            role="db",
            trust_level="high",
        )

        self.remote_adapter = IntegrationRemoteAdapter()
        self.manager = ConnectorManager(self.inventory)
        self.manager.register_adapter(
            name="local-shell",
            adapter=IntegrationLocalAdapter(),
            transport="local",
            make_default=True,
        )
        self.manager.register_adapter(
            name="remote-ssh",
            adapter=self.remote_adapter,
            transport="remote",
            make_default=True,
        )

    def test_multi_host_fanout_and_structured_reporting(self) -> None:
        orchestrator = ParallelHostOrchestrator(self.manager, max_concurrency=2)
        requests = [
            HostOperationRequest(
                host_id=host.host_id,
                operation="collect_status",
                payload={"command": "uptime"},
            )
            for host in (self.app_1, self.app_2, self.db_1)
        ]

        execution = orchestrator.execute_requests(requests)
        report = ControlPlaneResultReporter().aggregate(execution)

        self.assertEqual(execution.total_requests, 3)
        self.assertEqual(execution.succeeded, 3)
        self.assertEqual(execution.failed, 0)
        self.assertEqual(report.by_status["success"], 3)
        self.assertEqual(len(report.hosts), 3)

    def test_partial_failure_runs_deploy_rollback_routine(self) -> None:
        self.remote_adapter.failures_remaining[(self.app_2.host_id, "deploy_release")] = 1
        rollback = RollbackActionManager(self.manager)

        result = rollback.run_deploy(
            host_id=self.app_2.host_id,
            release_id="release-2026-04-14",
            previous_release_id="release-2026-04-10",
        )

        self.assertEqual(result.status, "rolled_back")
        host_calls = [
            call
            for call in self.remote_adapter.calls
            if call["host_id"] == self.app_2.host_id
        ]
        self.assertEqual(host_calls[0]["operation"], "deploy_release")
        self.assertEqual(host_calls[1]["operation"], "recover_release")
        self.assertIn("deployctl rollback release-2026-04-10", host_calls[1]["payload"]["command"])

    def test_dry_run_gate_requires_preview_before_destructive_execute(self) -> None:
        gate = DryRunExecutionGate(self.manager)

        with self.assertRaises(DryRunGateError):
            gate.execute(
                self.app_1.host_id,
                "restart_service",
                payload={"command": "systemctl restart api"},
            )

        preview_outcome = gate.execute(
            self.app_1.host_id,
            "restart_service",
            payload={"command": "systemctl restart api"},
            dry_run=True,
        )
        execute_outcome = gate.execute(
            self.app_1.host_id,
            "restart_service",
            payload={"command": "systemctl restart api"},
            dry_run_token=preview_outcome.preview.token,
        )

        self.assertEqual(preview_outcome.mode, "dry_run")
        self.assertEqual(execute_outcome.mode, "execute")
        self.assertIsNotNone(execute_outcome.execution)


if __name__ == "__main__":
    unittest.main()
