"""Tests for P4-T2 connector manager and adapter routing behavior."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control_plane import ConnectorManager, ConnectorManagerError, HostInventoryService, HostRecord


class RecordingAdapter:
    def __init__(self) -> None:
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
        return {
            "status": "ok",
            "host": host.hostname,
            "operation": operation,
            "identity": identity,
        }


class ConnectorManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = HostInventoryService()
        self.local_host = self.inventory.register_host(
            hostname="laptop-main",
            address="127.0.0.1",
            role="local",
            trust_level="high",
            labels=["primary"],
        )
        self.remote_app_host = self.inventory.register_host(
            hostname="app-1",
            address="10.0.0.10",
            role="app",
            trust_level="medium",
            labels=["prod"],
        )

        self.local_adapter = RecordingAdapter()
        self.remote_adapter = RecordingAdapter()

        self.manager = ConnectorManager(self.inventory)
        self.manager.register_adapter(
            name="local-shell",
            adapter=self.local_adapter,
            transport="local",
            make_default=True,
        )
        self.manager.register_adapter(
            name="remote-ssh",
            adapter=self.remote_adapter,
            transport="remote",
            host_roles=["app", "db", "worker", "gateway", "cache"],
            min_trust_level="medium",
            make_default=True,
        )

    def test_execute_uses_local_and_remote_defaults(self) -> None:
        local_result = self.manager.execute(
            self.local_host.host_id,
            "collect_status",
            payload={"verbose": True},
        )
        remote_result = self.manager.execute(
            self.remote_app_host.host_id,
            "restart_service",
            payload={"service": "api"},
        )

        self.assertEqual(local_result.adapter_name, "local-shell")
        self.assertEqual(local_result.transport, "local")
        self.assertEqual(remote_result.adapter_name, "remote-ssh")
        self.assertEqual(remote_result.transport, "remote")
        self.assertEqual(len(self.local_adapter.calls), 1)
        self.assertEqual(len(self.remote_adapter.calls), 1)
        self.assertEqual(self.local_adapter.calls[0]["operation"], "collect_status")
        self.assertEqual(self.remote_adapter.calls[0]["operation"], "restart_service")

    def test_transport_mismatch_is_rejected(self) -> None:
        with self.assertRaises(ConnectorManagerError):
            self.manager.execute(
                self.remote_app_host.host_id,
                "collect_status",
                adapter_name="local-shell",
            )

    def test_role_scope_is_enforced(self) -> None:
        db_only_adapter = RecordingAdapter()
        self.manager.register_adapter(
            name="remote-db-only",
            adapter=db_only_adapter,
            transport="remote",
            host_roles=["db"],
        )

        with self.assertRaises(ConnectorManagerError):
            self.manager.execute(
                self.remote_app_host.host_id,
                "collect_status",
                adapter_name="remote-db-only",
            )

    def test_minimum_trust_level_is_enforced(self) -> None:
        low_trust_worker = self.inventory.register_host(
            hostname="worker-low",
            address="10.0.0.11",
            role="worker",
            trust_level="low",
        )

        with self.assertRaises(ConnectorManagerError):
            self.manager.execute(low_trust_worker.host_id, "collect_status")

    def test_identity_mapping_prefers_host_over_role(self) -> None:
        self.manager.set_identity_mapping(role="app", connector_identity="svc-app-role")
        self.manager.set_identity_mapping(
            host_id=self.remote_app_host.host_id,
            connector_identity="svc-app-host",
        )

        result = self.manager.execute(self.remote_app_host.host_id, "collect_status")

        self.assertEqual(result.identity, "svc-app-host")
        self.assertEqual(self.remote_adapter.calls[0]["identity"], "svc-app-host")

    def test_disabled_host_is_rejected(self) -> None:
        self.inventory.update_host(self.remote_app_host.host_id, enabled=False)

        with self.assertRaises(ConnectorManagerError):
            self.manager.execute(self.remote_app_host.host_id, "collect_status")

    def test_adapter_requires_execute_callable(self) -> None:
        with self.assertRaises(ConnectorManagerError):
            self.manager.register_adapter(name="invalid-adapter", adapter=object(), transport="remote")

    def test_missing_default_remote_adapter_is_rejected(self) -> None:
        manager = ConnectorManager(self.inventory)
        manager.register_adapter(
            name="local-shell",
            adapter=RecordingAdapter(),
            transport="local",
            make_default=True,
        )

        with self.assertRaises(ConnectorManagerError):
            manager.execute(self.remote_app_host.host_id, "collect_status")


if __name__ == "__main__":
    unittest.main()
