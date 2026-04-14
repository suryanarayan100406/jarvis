"""Tests for P4-T3 SSH remote connector and key isolation controls."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control_plane import (
    ConnectorManager,
    HostInventoryService,
    HostRecord,
    SshConnectorError,
    SshExecutionRequest,
    SshRemoteConnector,
)


class RecordingSshTransport:
    def __init__(self) -> None:
        self.requests: list[SshExecutionRequest] = []

    def run(self, request: SshExecutionRequest) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "status": "ok",
            "exit_code": 0,
            "stdout": "done",
            "stderr": "",
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
            "transport": "local",
            "host": host.hostname,
            "operation": operation,
            "identity": identity,
            "payload": dict(payload),
        }


class SshRemoteConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = HostInventoryService()
        self.local_host = self.inventory.register_host(
            hostname="laptop-main",
            address="127.0.0.1",
            role="local",
            trust_level="high",
        )
        self.app_host = self.inventory.register_host(
            hostname="app-1",
            address="10.0.0.10",
            role="app",
            trust_level="high",
        )
        self.db_host = self.inventory.register_host(
            hostname="db-1",
            address="10.0.0.11",
            role="db",
            trust_level="high",
        )

        self.transport = RecordingSshTransport()
        self.connector = SshRemoteConnector(transport=self.transport)

    def test_execute_uses_host_bound_key_material(self) -> None:
        self.connector.configure_host_key(
            host_id=self.app_host.host_id,
            username="ops",
            private_key_ref="key-app-01",
            allowed_operations=["collect_status"],
        )

        result = self.connector.execute(
            host=self.app_host,
            operation="collect_status",
            payload={"command": "systemctl status api"},
            identity="svc-app",
        )

        self.assertEqual(result["transport"], "ssh")
        self.assertEqual(result["key_ref"], "key-app-01")
        self.assertEqual(len(self.transport.requests), 1)
        request = self.transport.requests[0]
        self.assertEqual(request.host_id, self.app_host.host_id)
        self.assertEqual(request.private_key_ref, "key-app-01")
        self.assertEqual(request.command, "systemctl status api")
        self.assertEqual(request.identity, "svc-app")

    def test_private_key_cannot_be_reused_across_hosts(self) -> None:
        self.connector.configure_host_key(
            host_id=self.app_host.host_id,
            username="ops",
            private_key_ref="shared-key",
        )

        with self.assertRaises(SshConnectorError):
            self.connector.configure_host_key(
                host_id=self.db_host.host_id,
                username="ops-db",
                private_key_ref="shared-key",
            )

    def test_operation_allowlist_is_enforced_per_host(self) -> None:
        self.connector.configure_host_key(
            host_id=self.app_host.host_id,
            username="ops",
            private_key_ref="key-app-01",
            allowed_operations=["collect_status"],
        )

        with self.assertRaises(SshConnectorError):
            self.connector.execute(
                host=self.app_host,
                operation="restart_service",
                payload={"command": "systemctl restart api"},
            )

    def test_cross_host_payload_replay_attempt_is_blocked(self) -> None:
        self.connector.configure_host_key(
            host_id=self.app_host.host_id,
            username="ops",
            private_key_ref="key-app-01",
            allowed_operations=["collect_status"],
        )

        with self.assertRaises(SshConnectorError):
            self.connector.execute(
                host=self.app_host,
                operation="collect_status",
                payload={
                    "command": "systemctl status api",
                    "host_id": self.db_host.host_id,
                },
            )

    def test_missing_credentials_for_host_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            self.connector.execute(
                host=self.app_host,
                operation="collect_status",
                payload={"command": "uptime"},
            )

    def test_local_host_is_rejected(self) -> None:
        with self.assertRaises(SshConnectorError):
            self.connector.execute(
                host=self.local_host,
                operation="collect_status",
                payload={"command": "uptime"},
            )

    def test_remove_host_key_revokes_future_execution(self) -> None:
        self.connector.configure_host_key(
            host_id=self.app_host.host_id,
            username="ops",
            private_key_ref="key-app-01",
            allowed_operations=["collect_status"],
        )

        self.connector.remove_host_key(self.app_host.host_id)

        with self.assertRaises(KeyError):
            self.connector.execute(
                host=self.app_host,
                operation="collect_status",
                payload={"command": "uptime"},
            )

    def test_connector_manager_integration_routes_remote_to_ssh_connector(self) -> None:
        self.connector.configure_host_key(
            host_id=self.app_host.host_id,
            username="ops",
            private_key_ref="key-app-01",
            allowed_operations=["collect_status"],
        )

        manager = ConnectorManager(self.inventory)
        manager.register_adapter(
            name="local-shell",
            adapter=LocalAdapter(),
            transport="local",
            make_default=True,
        )
        manager.register_adapter(
            name="remote-ssh",
            adapter=self.connector,
            transport="remote",
            make_default=True,
        )
        manager.set_identity_mapping(role="app", connector_identity="svc-app-role")

        result = manager.execute(
            self.app_host.host_id,
            "collect_status",
            payload={"command": "uptime"},
        )

        self.assertEqual(result.adapter_name, "remote-ssh")
        self.assertEqual(result.transport, "remote")
        self.assertEqual(result.identity, "svc-app-role")
        self.assertEqual(result.result["transport"], "ssh")
        self.assertEqual(self.transport.requests[0].identity, "svc-app-role")


if __name__ == "__main__":
    unittest.main()
