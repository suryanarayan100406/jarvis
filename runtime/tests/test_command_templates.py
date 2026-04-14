"""Tests for P4-T4 role-scoped command template library."""

from __future__ import annotations

import unittest

from runtime.control_plane import CommandTemplateError, CommandTemplateLibrary, HostInventoryService


class CommandTemplateLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.library = CommandTemplateLibrary()
        self.inventory = HostInventoryService()

    def test_register_and_list_templates(self) -> None:
        self.library.register_template(
            template_id="app.restart",
            operation="restart_service",
            command_template="systemctl restart {service}",
            host_roles=["app"],
            description="Restart an application service",
        )
        self.library.register_template(
            template_id="db.status",
            operation="collect_status",
            command_template="systemctl status {service}",
            host_roles=["db", "app"],
        )

        app_templates = self.library.list_templates(host_role="app")
        restart_templates = self.library.list_templates(operation="restart_service")

        self.assertEqual(len(app_templates), 2)
        self.assertEqual(len(restart_templates), 1)
        self.assertEqual(restart_templates[0].template_id, "app.restart")

    def test_register_rejects_duplicate_template_id(self) -> None:
        self.library.register_template(
            template_id="app.restart",
            operation="restart_service",
            command_template="systemctl restart {service}",
            host_roles=["app"],
        )

        with self.assertRaises(CommandTemplateError):
            self.library.register_template(
                template_id="app.restart",
                operation="restart_service",
                command_template="systemctl restart {service}",
                host_roles=["app"],
            )

    def test_register_rejects_unknown_role(self) -> None:
        with self.assertRaises(CommandTemplateError):
            self.library.register_template(
                template_id="invalid.role",
                operation="collect_status",
                command_template="uptime",
                host_roles=["unknown_role"],
            )

    def test_resolve_template_for_host_role(self) -> None:
        self.library.register_template(
            template_id="app.restart",
            operation="restart_service",
            command_template="systemctl restart {service}",
            host_roles=["app"],
        )

        resolved = self.library.resolve_template(
            "app.restart",
            host_role="app",
            parameters={"service": "api"},
        )

        self.assertEqual(resolved.operation, "restart_service")
        self.assertEqual(resolved.host_role, "app")
        self.assertEqual(resolved.command, "systemctl restart api")

    def test_resolve_rejects_missing_parameters(self) -> None:
        self.library.register_template(
            template_id="app.restart",
            operation="restart_service",
            command_template="systemctl restart {service}",
            host_roles=["app"],
        )

        with self.assertRaises(CommandTemplateError):
            self.library.resolve_template("app.restart", host_role="app", parameters={})

    def test_resolve_rejects_disallowed_host_role(self) -> None:
        self.library.register_template(
            template_id="db.backup",
            operation="run_backup",
            command_template="pg_dump {database}",
            host_roles=["db"],
        )

        with self.assertRaises(CommandTemplateError):
            self.library.resolve_template(
                "db.backup",
                host_role="app",
                parameters={"database": "main"},
            )

    def test_operation_allowlist_matching_by_role(self) -> None:
        self.library.register_template(
            template_id="app.status",
            operation="collect_status",
            command_template="systemctl status {service}",
            host_roles=["app"],
        )
        self.library.register_template(
            template_id="app.restart",
            operation="restart_service",
            command_template="systemctl restart {service}",
            host_roles=["app"],
        )
        self.library.register_template(
            template_id="db.status",
            operation="collect_status",
            command_template="systemctl status {service}",
            host_roles=["db"],
        )

        self.assertTrue(self.library.is_operation_allowed(host_role="app", operation="collect_status"))
        self.assertTrue(self.library.is_operation_allowed(host_role="app", operation="restart_service"))
        self.assertFalse(self.library.is_operation_allowed(host_role="app", operation="run_backup"))
        self.assertEqual(self.library.allowed_operations(host_role="app"), ("collect_status", "restart_service"))

    def test_resolve_blocks_control_character_in_parameter(self) -> None:
        self.library.register_template(
            template_id="app.restart",
            operation="restart_service",
            command_template="systemctl restart {service}",
            host_roles=["app"],
        )

        with self.assertRaises(CommandTemplateError):
            self.library.resolve_template(
                "app.restart",
                host_role="app",
                parameters={"service": "api\nrm -rf /"},
            )

    def test_resolve_for_host_uses_inventory_role(self) -> None:
        host = self.inventory.register_host(
            hostname="app-1",
            address="10.0.0.20",
            role="app",
            trust_level="high",
        )
        self.library.register_template(
            template_id="app.status",
            operation="collect_status",
            command_template="systemctl status {service}",
            host_roles=["app"],
        )

        resolved = self.library.resolve_for_host(
            "app.status",
            host=host,
            parameters={"service": "api"},
        )

        self.assertEqual(resolved.host_role, "app")
        self.assertEqual(resolved.command, "systemctl status api")


if __name__ == "__main__":
    unittest.main()
