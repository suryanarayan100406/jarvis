"""Tests for P4-T1 host inventory service."""

from __future__ import annotations

import unittest

from runtime.control_plane import HostInventoryError, HostInventoryService


class HostInventoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = HostInventoryService()

    def test_register_and_get_host(self) -> None:
        record = self.inventory.register_host(
            hostname="laptop-main",
            address="127.0.0.1",
            role="local",
            trust_level="high",
            labels=["primary", "dev"],
        )

        loaded = self.inventory.get_host(record.host_id)

        self.assertEqual(loaded.hostname, "laptop-main")
        self.assertEqual(loaded.role, "local")
        self.assertEqual(loaded.trust_level, "high")
        self.assertEqual(loaded.labels, ("dev", "primary"))

    def test_duplicate_hostname_or_address_rejected(self) -> None:
        self.inventory.register_host(
            hostname="server-a",
            address="10.0.0.2",
            role="app",
            trust_level="medium",
        )

        with self.assertRaises(HostInventoryError):
            self.inventory.register_host(
                hostname="server-a",
                address="10.0.0.3",
                role="app",
                trust_level="medium",
            )

        with self.assertRaises(HostInventoryError):
            self.inventory.register_host(
                hostname="server-b",
                address="10.0.0.2",
                role="app",
                trust_level="medium",
            )

    def test_list_filters_by_role_trust_and_label(self) -> None:
        self.inventory.register_host(
            hostname="app-1",
            address="10.0.0.10",
            role="app",
            trust_level="high",
            labels=["prod"],
        )
        self.inventory.register_host(
            hostname="db-1",
            address="10.0.0.11",
            role="db",
            trust_level="high",
            labels=["prod", "critical"],
        )
        self.inventory.register_host(
            hostname="worker-1",
            address="10.0.0.12",
            role="worker",
            trust_level="low",
            labels=["batch"],
        )

        by_role = self.inventory.list_hosts(role="db")
        by_trust = self.inventory.list_hosts(trust_level="high")
        by_label = self.inventory.list_hosts(label="critical")

        self.assertEqual(len(by_role), 1)
        self.assertEqual(by_role[0].hostname, "db-1")
        self.assertEqual(len(by_trust), 2)
        self.assertEqual(len(by_label), 1)
        self.assertEqual(by_label[0].hostname, "db-1")

    def test_update_host_changes_role_labels_and_enabled(self) -> None:
        record = self.inventory.register_host(
            hostname="cache-1",
            address="10.0.0.20",
            role="cache",
            trust_level="medium",
        )

        updated = self.inventory.update_host(
            record.host_id,
            role="worker",
            trust_level="low",
            labels=["batch", "nightly"],
            enabled=False,
            metadata={"owner": "ops"},
        )

        self.assertEqual(updated.role, "worker")
        self.assertEqual(updated.trust_level, "low")
        self.assertEqual(updated.labels, ("batch", "nightly"))
        self.assertFalse(updated.enabled)
        self.assertEqual(updated.metadata["owner"], "ops")

    def test_enabled_only_filter(self) -> None:
        first = self.inventory.register_host(
            hostname="host-enabled",
            address="10.0.0.30",
            role="app",
            trust_level="high",
        )
        second = self.inventory.register_host(
            hostname="host-disabled",
            address="10.0.0.31",
            role="app",
            trust_level="high",
        )
        self.inventory.update_host(second.host_id, enabled=False)

        enabled_hosts = self.inventory.list_hosts(enabled_only=True)

        self.assertEqual(len(enabled_hosts), 1)
        self.assertEqual(enabled_hosts[0].host_id, first.host_id)

    def test_remove_host(self) -> None:
        record = self.inventory.register_host(
            hostname="host-remove",
            address="10.0.0.40",
            role="gateway",
            trust_level="medium",
        )

        self.inventory.remove_host(record.host_id)

        with self.assertRaises(KeyError):
            self.inventory.get_host(record.host_id)


if __name__ == "__main__":
    unittest.main()
