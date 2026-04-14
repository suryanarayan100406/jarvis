"""Tests for P4-T5 control-plane policy overlay scoping behavior."""

from __future__ import annotations

import unittest

from runtime.control_plane import ControlPlanePolicyOverlay, ControlPlanePolicyRequest, HostInventoryService


class ControlPlanePolicyOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = HostInventoryService()
        self.app_host = self.inventory.register_host(
            hostname="app-1",
            address="10.0.0.10",
            role="app",
            trust_level="high",
            labels=["prod"],
        )
        self.db_host = self.inventory.register_host(
            hostname="db-1",
            address="10.0.0.11",
            role="db",
            trust_level="high",
            labels=["prod"],
        )
        self.overlay = ControlPlanePolicyOverlay()

    def test_base_policy_allows_without_overlay_restrictions(self) -> None:
        decision = self.overlay.evaluate(
            self._request(
                operator_id="boss",
                operator_role="primary_user",
                host=self.app_host,
                operation="collect_status",
                command="systemctl status api",
                environment="dev",
            )
        )

        self.assertEqual(decision.decision, "allow")

    def test_host_scope_denies_operation_not_allowlisted(self) -> None:
        self.overlay.set_host_policy(
            host_id=self.app_host.host_id,
            allowed_operations=["collect_status"],
        )

        decision = self.overlay.evaluate(
            self._request(
                operator_id="boss",
                operator_role="primary_user",
                host=self.app_host,
                operation="restart_service",
                command="systemctl restart api",
                environment="dev",
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "overlay.host.operation.deny")

    def test_host_scope_restricts_operators(self) -> None:
        self.overlay.set_host_policy(
            host_id=self.app_host.host_id,
            allowed_operators=["boss"],
        )

        decision = self.overlay.evaluate(
            self._request(
                operator_id="ops-2",
                operator_role="authorized_operator",
                host=self.app_host,
                operation="collect_status",
                command="systemctl status api",
                environment="dev",
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "overlay.host.operator.deny")

    def test_operator_scope_denies_disallowed_host_role(self) -> None:
        self.overlay.set_operator_policy(
            operator_id="ops-1",
            allowed_host_roles=["db"],
        )

        decision = self.overlay.evaluate(
            self._request(
                operator_id="ops-1",
                operator_role="authorized_operator",
                host=self.app_host,
                operation="collect_status",
                command="systemctl status api",
                environment="dev",
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "overlay.operator.host_role.deny")

    def test_role_scope_can_require_approval(self) -> None:
        self.overlay.set_role_policy(
            operator_role="authorized_operator",
            require_approval_operations=["restart_service"],
        )

        decision = self.overlay.evaluate(
            self._request(
                operator_id="ops-1",
                operator_role="authorized_operator",
                host=self.app_host,
                operation="restart_service",
                command="systemctl restart api",
                environment="dev",
            )
        )

        self.assertEqual(decision.decision, "require_approval")
        self.assertEqual(decision.rule_id, "overlay.role.operation.require_approval")

    def test_command_scope_blocks_disallowed_substring(self) -> None:
        self.overlay.set_command_policy(
            operation="collect_status",
            blocked_substrings=["/etc/shadow"],
        )

        decision = self.overlay.evaluate(
            self._request(
                operator_id="boss",
                operator_role="primary_user",
                host=self.app_host,
                operation="collect_status",
                command="cat /etc/shadow",
                environment="dev",
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "overlay.command.substring.deny")

    def test_command_scope_blocks_unapproved_prefix(self) -> None:
        self.overlay.set_command_policy(
            operation="collect_status",
            allowed_prefixes=["systemctl status", "uptime"],
        )

        decision = self.overlay.evaluate(
            self._request(
                operator_id="boss",
                operator_role="primary_user",
                host=self.app_host,
                operation="collect_status",
                command="cat /var/log/syslog",
                environment="dev",
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "overlay.command.prefix.deny")

    def test_global_blocked_tokens_are_denied(self) -> None:
        decision = self.overlay.evaluate(
            self._request(
                operator_id="boss",
                operator_role="primary_user",
                host=self.app_host,
                operation="collect_status",
                command="uptime; rm -rf /",
                environment="dev",
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.rule_id, "overlay.command.global_token.deny")

    def test_base_policy_deny_is_preserved(self) -> None:
        decision = self.overlay.evaluate(
            self._request(
                operator_id="guest-1",
                operator_role="limited_user",
                host=self.app_host,
                operation="delete_deployment",
                command="rm -rf /srv/app/releases/old",
                environment="prod",
            )
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.base_rule_id, "policy.limited.high_critical.deny")

    @staticmethod
    def _request(
        *,
        operator_id: str,
        operator_role: str,
        host,
        operation: str,
        command: str,
        environment: str,
    ) -> ControlPlanePolicyRequest:
        return ControlPlanePolicyRequest(
            operator_id=operator_id,
            operator_role=operator_role,
            host=host,
            operation=operation,
            command=command,
            environment=environment,
        )


if __name__ == "__main__":
    unittest.main()
