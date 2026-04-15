"""Tests for P11-T4 backup strategy for state, memory, and configuration."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.orchestration import (
    BackupDatasetPolicy,
    BackupStrategyError,
    BackupStrategyManager,
    build_default_backup_strategy_profile,
    validate_backup_strategy_profile,
)


class BackupStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = build_default_backup_strategy_profile()
        self.manager = BackupStrategyManager(self.profile)

    def test_default_profile_covers_required_datasets(self) -> None:
        validate_backup_strategy_profile(self.profile)

        dataset_ids = {policy.dataset_id for policy in self.profile.dataset_policies}
        self.assertEqual(dataset_ids, {"state", "memory", "configuration"})

    def test_profile_manifest_is_deterministic(self) -> None:
        first = self.profile.to_manifest()
        second = self.profile.to_manifest()
        self.assertEqual(first, second)

    def test_run_backup_generates_completed_record_and_snapshots(self) -> None:
        payloads = {
            "state": "phase:11\nnext:P11-T4",
            "memory": "context and preferences",
            "configuration": "policy=true\nstrict=true",
        }

        record = self.manager.run_backup(payloads)

        self.assertEqual(record.status, "completed")
        self.assertEqual(record.failure_reason, None)
        self.assertEqual(len(record.snapshots), 3)
        self.assertTrue(all(snapshot.content_digest for snapshot in record.snapshots))

    def test_backup_digest_changes_when_payload_changes(self) -> None:
        baseline = self.manager.run_backup(
            {
                "state": "alpha",
                "memory": "beta",
                "configuration": "gamma",
            }
        )
        changed = self.manager.run_backup(
            {
                "state": "alpha-2",
                "memory": "beta",
                "configuration": "gamma",
            }
        )

        self.assertNotEqual(baseline.deterministic_digest, changed.deterministic_digest)

    def test_run_backup_rejects_missing_required_payload(self) -> None:
        with self.assertRaises(BackupStrategyError):
            self.manager.run_backup(
                {
                    "state": "ok",
                    "memory": "ok",
                }
            )

    def test_run_backup_can_fail_on_payload_size_limit(self) -> None:
        state_policy = self.profile.get_policy("state")
        constrained_policy = replace(state_policy, max_snapshot_bytes=1024)
        modified_profile = replace(
            self.profile,
            dataset_policies=tuple(
                constrained_policy if policy.dataset_id == "state" else policy
                for policy in self.profile.dataset_policies
            ),
        )
        manager = BackupStrategyManager(modified_profile)

        record = manager.run_backup(
            {
                "state": "x" * 2048,
                "memory": "ok",
                "configuration": "ok",
            },
            fail_on_size=True,
        )

        self.assertEqual(record.status, "failed")
        self.assertIn("exceeds max_snapshot_bytes", record.failure_reason or "")

    def test_validation_rejects_missing_required_dataset_policy(self) -> None:
        invalid_profile = replace(
            self.profile,
            dataset_policies=tuple(
                policy for policy in self.profile.dataset_policies if policy.dataset_id != "memory"
            ),
        )

        with self.assertRaises(BackupStrategyError):
            validate_backup_strategy_profile(invalid_profile)

    def test_validation_rejects_duplicate_dataset_ids(self) -> None:
        duplicate = replace(self.profile.dataset_policies[0], source_path="duplicate")
        invalid_profile = replace(
            self.profile,
            dataset_policies=(duplicate, *self.profile.dataset_policies[1:], duplicate),
        )

        with self.assertRaises(BackupStrategyError):
            validate_backup_strategy_profile(invalid_profile)

    def test_validation_rejects_invalid_integrity_algorithm(self) -> None:
        bad_policy = BackupDatasetPolicy(
            dataset_id="state",
            source_path=".planning/STATE.md",
            cadence_minutes=60,
            retention_points=168,
            encryption_required=True,
            integrity_algorithm="md5",
            max_snapshot_bytes=1_000_000,
            metadata={},
        )

        invalid_profile = replace(
            self.profile,
            dataset_policies=(bad_policy, *self.profile.dataset_policies[1:]),
        )

        with self.assertRaises(BackupStrategyError):
            validate_backup_strategy_profile(invalid_profile)


if __name__ == "__main__":
    unittest.main()