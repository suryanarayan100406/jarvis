"""Tests for P4-T6 dry-run execution gate."""

from __future__ import annotations

import unittest
from typing import Any

from runtime.control_plane import ConnectorManager, DryRunExecutionGate, DryRunGateError, HostInventoryService, HostRecord


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
            "identity": identity,
        }


class DryRunExecutionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = HostInventoryService()
        self.local_host = self.inventory.register_host(
            hostname="laptop-main",
            address="127.0.0.1",
            role="local",
            trust_level="high",
        )
        self.remote_host = self.inventory.register_host(
            hostname="app-1",
            address="10.0.0.10",
            role="app",
            trust_level="high",
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
            make_default=True,
        )
        self.gate = DryRunExecutionGate(self.manager)

    def test_dry_run_mode_returns_preview_without_execution(self) -> None:
        outcome = self.gate.execute(
            self.remote_host.host_id,
            "restart_service",
            payload={"command": "systemctl restart api"},
            dry_run=True,
        )

        self.assertEqual(outcome.mode, "dry_run")
        self.assertIsNotNone(outcome.preview)
        self.assertTrue(outcome.preview.is_destructive)
        self.assertIsNone(outcome.execution)
        self.assertEqual(len(self.remote_adapter.calls), 0)

    def test_destructive_execution_requires_token(self) -> None:
        with self.assertRaises(DryRunGateError):
            self.gate.execute(
                self.remote_host.host_id,
                "restart_service",
                payload={"command": "systemctl restart api"},
            )

    def test_destructive_execution_succeeds_with_matching_token(self) -> None:
        preview = self.gate.preview_operation(
            self.remote_host.host_id,
            "restart_service",
            payload={"command": "systemctl restart api"},
            identity="svc-app",
        )

        outcome = self.gate.execute(
            self.remote_host.host_id,
            "restart_service",
            payload={"command": "systemctl restart api"},
            identity="svc-app",
            dry_run_token=preview.token,
        )

        self.assertEqual(outcome.mode, "execute")
        self.assertIsNotNone(outcome.execution)
        self.assertEqual(len(self.remote_adapter.calls), 1)
        self.assertEqual(self.remote_adapter.calls[0]["identity"], "svc-app")

    def test_dry_run_token_is_single_use(self) -> None:
        preview = self.gate.preview_operation(
            self.remote_host.host_id,
            "restart_service",
            payload={"command": "systemctl restart api"},
        )

        self.gate.execute(
            self.remote_host.host_id,
            "restart_service",
            payload={"command": "systemctl restart api"},
            dry_run_token=preview.token,
        )

        with self.assertRaises(DryRunGateError):
            self.gate.execute(
                self.remote_host.host_id,
                "restart_service",
                payload={"command": "systemctl restart api"},
                dry_run_token=preview.token,
            )

    def test_non_destructive_operation_executes_without_preview(self) -> None:
        outcome = self.gate.execute(
            self.remote_host.host_id,
            "collect_status",
            payload={"command": "uptime"},
        )

        self.assertEqual(outcome.mode, "execute")
        self.assertIsNone(outcome.preview)
        self.assertIsNotNone(outcome.execution)
        self.assertEqual(len(self.remote_adapter.calls), 1)

    def test_payload_mismatch_rejects_token(self) -> None:
        preview = self.gate.preview_operation(
            self.remote_host.host_id,
            "restart_service",
            payload={"command": "systemctl restart api"},
        )

        with self.assertRaises(DryRunGateError):
            self.gate.execute(
                self.remote_host.host_id,
                "restart_service",
                payload={"command": "systemctl restart worker"},
                dry_run_token=preview.token,
            )

    def test_command_keyword_can_mark_operation_destructive(self) -> None:
        preview = self.gate.preview_operation(
            self.remote_host.host_id,
            "collect_status",
            payload={"command": "rm -rf /tmp/data"},
        )

        self.assertTrue(preview.is_destructive)

        with self.assertRaises(DryRunGateError):
            self.gate.execute(
                self.remote_host.host_id,
                "collect_status",
                payload={"command": "rm -rf /tmp/data"},
            )

    def test_adapter_name_and_identity_are_token_scoped(self) -> None:
        preview = self.gate.preview_operation(
            self.remote_host.host_id,
            "restart_service",
            payload={"command": "systemctl restart api"},
            adapter_name="remote-ssh",
            identity="svc-app",
        )

        with self.assertRaises(DryRunGateError):
            self.gate.execute(
                self.remote_host.host_id,
                "restart_service",
                payload={"command": "systemctl restart api"},
                adapter_name="remote-ssh",
                identity="svc-app-2",
                dry_run_token=preview.token,
            )


if __name__ == "__main__":
    unittest.main()
