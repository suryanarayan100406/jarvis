"""Tests for P4-T7 rollback actions manager."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control_plane import ConnectorManager, HostInventoryService, HostRecord, RollbackActionManager


class FaultInjectingAdapter:
    def __init__(self, fail_operations: set[str] | None = None) -> None:
        self.fail_operations = set(fail_operations or set())
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
        if operation in self.fail_operations:
            raise RuntimeError(f"forced_failure:{operation}")

        return {
            "status": "ok",
            "operation": operation,
            "host": host.hostname,
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
            "operation": operation,
            "host": host.hostname,
            "payload": dict(payload),
        }


class RollbackActionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = HostInventoryService()
        self.host = self.inventory.register_host(
            hostname="app-1",
            address="10.0.0.10",
            role="app",
            trust_level="high",
        )
        self.local_host = self.inventory.register_host(
            hostname="laptop-main",
            address="127.0.0.1",
            role="local",
            trust_level="high",
        )

    def test_service_restart_success_without_rollback(self) -> None:
        remote_adapter = FaultInjectingAdapter()
        manager = self._build_manager(remote_adapter)

        result = manager.run_service_restart(host_id=self.host.host_id, service="api")

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(remote_adapter.calls), 1)
        self.assertEqual(remote_adapter.calls[0]["operation"], "restart_service")

    def test_service_restart_failure_triggers_rollback(self) -> None:
        remote_adapter = FaultInjectingAdapter(fail_operations={"restart_service"})
        manager = self._build_manager(remote_adapter)

        result = manager.run_service_restart(host_id=self.host.host_id, service="api")

        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(len(remote_adapter.calls), 2)
        self.assertEqual(remote_adapter.calls[0]["operation"], "restart_service")
        self.assertEqual(remote_adapter.calls[1]["operation"], "recover_service")
        self.assertIn("Forward action failed", result.error or "")

    def test_rollback_failure_is_reported(self) -> None:
        remote_adapter = FaultInjectingAdapter(
            fail_operations={"restart_service", "recover_service"}
        )
        manager = self._build_manager(remote_adapter)

        result = manager.run_service_restart(host_id=self.host.host_id, service="api")

        self.assertEqual(result.status, "rollback_failed")
        self.assertEqual(len(remote_adapter.calls), 2)
        self.assertIn("rollback failed", result.error or "")

    def test_deploy_failure_triggers_release_recovery(self) -> None:
        remote_adapter = FaultInjectingAdapter(fail_operations={"deploy_release"})
        manager = self._build_manager(remote_adapter)

        result = manager.run_deploy(
            host_id=self.host.host_id,
            release_id="release-2026-04-14",
            previous_release_id="release-2026-04-10",
        )

        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(len(remote_adapter.calls), 2)
        self.assertEqual(remote_adapter.calls[0]["operation"], "deploy_release")
        self.assertEqual(remote_adapter.calls[1]["operation"], "recover_release")
        self.assertIn("deployctl rollback release-2026-04-10", remote_adapter.calls[1]["payload"]["command"])

    def test_dry_run_returns_plan_without_execution(self) -> None:
        remote_adapter = FaultInjectingAdapter()
        manager = self._build_manager(remote_adapter)

        result = manager.run_service_restart(
            host_id=self.host.host_id,
            service="api",
            dry_run=True,
        )

        self.assertEqual(result.status, "dry_run")
        self.assertEqual(len(remote_adapter.calls), 0)

    def test_get_plan_returns_built_routine(self) -> None:
        remote_adapter = FaultInjectingAdapter()
        manager = self._build_manager(remote_adapter)

        plan = manager.build_service_restart_plan(host_id=self.host.host_id, service="api")
        loaded = manager.get_plan(plan.routine_id)

        self.assertEqual(loaded.routine_id, plan.routine_id)
        self.assertEqual(loaded.forward_action.operation, "restart_service")

    def _build_manager(self, remote_adapter: FaultInjectingAdapter) -> RollbackActionManager:
        connector_manager = ConnectorManager(self.inventory)
        connector_manager.register_adapter(
            name="local-shell",
            adapter=LocalAdapter(),
            transport="local",
            make_default=True,
        )
        connector_manager.register_adapter(
            name="remote-ssh",
            adapter=remote_adapter,
            transport="remote",
            make_default=True,
        )
        return RollbackActionManager(connector_manager)


if __name__ == "__main__":
    unittest.main()
