"""Tests for P7-T2 secret manager hardening and rotation workflows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from runtime.security import HardenedSecretManager, SecretManagerError


class HardenedSecretManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)

        def now_provider() -> datetime:
            return self.now

        self.manager = HardenedSecretManager(now_provider=now_provider)

    def test_create_secret_sets_rotation_due_and_hides_material(self) -> None:
        record = self.manager.create_secret(
            secret_id="db.password",
            value="AlphaBravo1234",
            owner_id="security",
            rotation_interval_days=15,
        )

        self.assertEqual(record.status, "active")
        self.assertEqual(record.version, 1)
        self.assertTrue(record.current_fingerprint)
        self.assertEqual(record.next_rotation_due, "2026-05-06T12:00:00Z")

    def test_read_secret_requires_scope_authorization(self) -> None:
        self.manager.create_secret(
            secret_id="api.key",
            value="SecureKey9999",
            owner_id="security",
            allowed_readers=("security", "runtime"),
        )

        result = self.manager.read_secret("api.key", actor_id="runtime", purpose="service_boot")
        self.assertIsNone(result.value)
        self.assertIn("***", result.masked_value)

        with self.assertRaises(SecretManagerError):
            self.manager.read_secret("api.key", actor_id="guest", purpose="debug")

    def test_rotate_secret_increments_version_and_audits(self) -> None:
        self.manager.create_secret(
            secret_id="token",
            value="TokenValue1234",
            owner_id="security",
            allowed_rotators=("security", "ops"),
        )

        rotation = self.manager.rotate_secret(
            "token",
            new_value="TokenValue5678",
            actor_id="ops",
            reason="scheduled_rotation",
        )
        record = self.manager.get_secret("token")

        self.assertEqual(rotation.previous_version, 1)
        self.assertEqual(rotation.new_version, 2)
        self.assertEqual(record.version, 2)
        self.assertNotEqual(rotation.previous_fingerprint, rotation.new_fingerprint)

        events = self.manager.list_audit_events(secret_id="token", event_type="rotated")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].actor_id, "ops")

    def test_due_for_rotation_selects_expired_secrets(self) -> None:
        self.manager.create_secret(
            secret_id="short",
            value="ShortLife1234",
            owner_id="security",
            rotation_interval_days=1,
        )
        self.manager.create_secret(
            secret_id="long",
            value="LongLife1234",
            owner_id="security",
            rotation_interval_days=10,
        )

        self.now = self.now + timedelta(days=2)
        due = self.manager.due_for_rotation()

        self.assertEqual([item.secret_id for item in due], ["short"])

    def test_revoke_secret_blocks_future_access_and_rotation(self) -> None:
        self.manager.create_secret(
            secret_id="revokable",
            value="Revokable1234",
            owner_id="security",
            allowed_rotators=("security",),
        )
        revoked = self.manager.revoke_secret("revokable", actor_id="security", reason="incident_containment")

        self.assertEqual(revoked.status, "revoked")
        with self.assertRaises(SecretManagerError):
            self.manager.read_secret("revokable", actor_id="security", purpose="check")
        with self.assertRaises(SecretManagerError):
            self.manager.rotate_secret(
                "revokable",
                new_value="Another12345",
                actor_id="security",
                reason="attempt",
            )

    def test_secret_hardening_validation_rejects_weak_values(self) -> None:
        with self.assertRaises(SecretManagerError):
            self.manager.create_secret(
                secret_id="weak",
                value="short1",
                owner_id="security",
            )

        with self.assertRaises(SecretManagerError):
            self.manager.create_secret(
                secret_id="weak2",
                value="onlylettersssss",
                owner_id="security",
            )

        with self.assertRaises(SecretManagerError):
            self.manager.create_secret(
                secret_id="weak3",
                value="12345678901234",
                owner_id="security",
            )


if __name__ == "__main__":
    unittest.main()
