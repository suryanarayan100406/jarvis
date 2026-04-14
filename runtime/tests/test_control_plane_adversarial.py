"""Adversarial tests for P4-T12 permission leakage and connector misuse controls."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control_plane import (
    CommandTemplateError,
    CommandTemplateLibrary,
    ConnectorManager,
    ConnectorManagerError,
    ControlPlanePolicyOverlay,
    ControlPlanePolicyRequest,
    DryRunExecutionGate,
    DryRunGateError,
    HostInventoryService,
    HostRecord,
    SshConnectorError,
    SshExecutionRequest,
    SshRemoteConnector,
)


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
                "operation": operation,
                "payload": dict(payload),
                "identity": identity,
            }
        )
        return {
            "status": "ok",
            "host": host.hostname,
            "operation": operation,
        }


class RecordingSshTransport:
    def __init__(self) -> None:
        self.requests: list[SshExecutionRequest] = []

    def run(self, request: SshExecutionRequest) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "status": "ok",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
        }


class ControlPlaneAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = HostInventoryService()
        self.local_host = self.inventory.register_host(
            hostname="laptop-main",
            address="127.0.0.1",
            role="local",
            trust_level="high",
        )
        self.app_high = self.inventory.register_host(
            hostname="app-1",
            address="10.0.0.10",
            role="app",
            trust_level="high",
        )
        self.db_high = self.inventory.register_host(
            hostname="db-1",
            address="10.0.0.11",
            role="db",
            trust_level="high",
        )
        self.worker_low = self.inventory.register_host(
            hostname="worker-low",
            address="10.0.0.12",
            role="worker",
            trust_level="low",
        )

    def test_low_trust_host_cannot_use_high_trust_adapter_scope(self) -> None:
        manager = ConnectorManager(self.inventory)
        manager.register_adapter(
            name="local-shell",
            adapter=RecordingAdapter(),
            transport="local",
            make_default=True,
        )
        manager.register_adapter(
            name="remote-sensitive",
            adapter=RecordingAdapter(),
            transport="remote",
            host_roles=["app", "db", "worker"],
            min_trust_level="high",
            make_default=True,
        )

        with self.assertRaises(ConnectorManagerError):
            manager.execute(
                self.worker_low.host_id,
                "collect_status",
                payload={"command": "uptime"},
            )

    def test_private_key_reuse_across_hosts_is_blocked(self) -> None:
        connector = SshRemoteConnector(transport=RecordingSshTransport())
        connector.configure_host_key(
            host_id=self.app_high.host_id,
            username="ops-app",
            private_key_ref="key-shared",
        )

        with self.assertRaises(SshConnectorError):
            connector.configure_host_key(
                host_id=self.db_high.host_id,
                username="ops-db",
                private_key_ref="key-shared",
            )

    def test_cross_host_payload_replay_is_blocked(self) -> None:
        connector = SshRemoteConnector(transport=RecordingSshTransport())
        connector.configure_host_key(
            host_id=self.app_high.host_id,
            username="ops-app",
            private_key_ref="key-app",
            allowed_operations=["collect_status"],
        )

        with self.assertRaises(SshConnectorError):
            connector.execute(
                host=self.app_high,
                operation="collect_status",
                payload={
                    "command": "systemctl status api",
                    "host_id": self.db_high.host_id,
                },
            )

    def test_command_template_injection_is_rejected(self) -> None:
        library = CommandTemplateLibrary()
        library.register_template(
            template_id="app.restart",
            operation="restart_service",
            command_template="systemctl restart {service}",
            host_roles=["app"],
        )

        with self.assertRaises(CommandTemplateError):
            library.resolve_template(
                "app.restart",
                host_role="app",
                parameters={"service": "api\nrm -rf /"},
            )

    def test_operator_scope_blocks_host_role_privilege_escalation(self) -> None:
        overlay = ControlPlanePolicyOverlay()
        overlay.set_operator_policy(
            operator_id="ops-junior",
            allowed_host_roles=["app"],
        )

        decision = overlay.evaluate(
            ControlPlanePolicyRequest(
                operator_id="ops-junior",
                operator_role="authorized_operator",
                host=self.db_high,
                operation="collect_status",
                command="uptime",
                environment="dev",
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "overlay.operator.host_role.deny")

    def test_dry_run_token_replay_attack_is_blocked(self) -> None:
        adapter = RecordingAdapter()
        manager = ConnectorManager(self.inventory)
        manager.register_adapter(
            name="local-shell",
            adapter=RecordingAdapter(),
            transport="local",
            make_default=True,
        )
        manager.register_adapter(
            name="remote-ssh",
            adapter=adapter,
            transport="remote",
            make_default=True,
        )

        gate = DryRunExecutionGate(manager)
        preview = gate.preview_operation(
            self.app_high.host_id,
            "restart_service",
            payload={"command": "systemctl restart api"},
        )

        gate.execute(
            self.app_high.host_id,
            "restart_service",
            payload={"command": "systemctl restart api"},
            dry_run_token=preview.token,
        )

        with self.assertRaises(DryRunGateError):
            gate.execute(
                self.app_high.host_id,
                "restart_service",
                payload={"command": "systemctl restart api"},
                dry_run_token=preview.token,
            )


if __name__ == "__main__":
    unittest.main()
