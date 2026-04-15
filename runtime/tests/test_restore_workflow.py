"""Tests for P11-T5 restore workflow with integrity checks."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.orchestration import (
    BackupStrategyManager,
    RestoreWorkflowEngine,
    RestoreWorkflowError,
    build_default_backup_strategy_profile,
)


class RestoreWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backup_manager = BackupStrategyManager(build_default_backup_strategy_profile())
        self.restore_engine = RestoreWorkflowEngine()

        self.payloads = {
            "state": "phase:11\nstep:P11-T5",
            "memory": "session memory checkpoint",
            "configuration": "strict_mode=true\npolicy=enforced",
        }
        self.backup_record = self.backup_manager.run_backup(self.payloads)

    def test_restore_succeeds_when_payload_integrity_matches(self) -> None:
        result = self.restore_engine.restore_from_backup(self.backup_record, self.payloads)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.restored_count, 3)
        self.assertEqual(result.failed_count, 0)
        self.assertTrue(all(step.integrity_verified for step in result.steps))

    def test_restore_fails_on_digest_mismatch(self) -> None:
        tampered_payloads = dict(self.payloads)
        tampered_payloads["memory"] = "tampered payload"

        result = self.restore_engine.restore_from_backup(
            self.backup_record,
            tampered_payloads,
            strict=True,
        )

        self.assertEqual(result.status, "failed")
        self.assertGreaterEqual(result.failed_count, 1)
        self.assertTrue(any(step.reason and "Integrity digest mismatch" in step.reason for step in result.steps))

    def test_restore_fails_when_payload_missing(self) -> None:
        incomplete_payloads = {
            "state": self.payloads["state"],
            "memory": self.payloads["memory"],
        }

        result = self.restore_engine.restore_from_backup(
            self.backup_record,
            incomplete_payloads,
            strict=False,
        )

        self.assertEqual(result.status, "failed")
        self.assertTrue(any(step.dataset_id == "configuration" and step.status == "failed" for step in result.steps))

    def test_restore_rejects_non_completed_backup_record(self) -> None:
        failed_record = replace(self.backup_record, status="failed")

        with self.assertRaises(RestoreWorkflowError):
            self.restore_engine.restore_from_backup(failed_record, self.payloads)

    def test_verify_restore_payload_returns_expected_result(self) -> None:
        snapshot = next(snapshot for snapshot in self.backup_record.snapshots if snapshot.dataset_id == "state")

        self.assertTrue(self.restore_engine.verify_restore_payload(snapshot, self.payloads["state"]))
        self.assertFalse(self.restore_engine.verify_restore_payload(snapshot, "different-state-payload"))

    def test_restore_manifest_is_deterministic(self) -> None:
        result = self.restore_engine.restore_from_backup(self.backup_record, self.payloads)

        first = result.to_manifest()
        second = result.to_manifest()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()