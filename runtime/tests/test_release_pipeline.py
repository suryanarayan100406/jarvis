"""Tests for P11-T7 release pipeline canary and rollback support."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.orchestration import (
    CanaryObservation,
    CanaryThresholdPolicy,
    ReleasePipelineError,
    ReleasePipelineManager,
    build_default_canary_threshold_policy,
    validate_canary_threshold_policy,
)


class ReleasePipelineManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = ReleasePipelineManager()
        self.pipeline = self.manager.create_pipeline(
            release_id="release-2026-04-20",
            previous_release_id="release-2026-04-19",
            canary_percentage=15,
        )

    def test_create_pipeline_initializes_pending_canary_state(self) -> None:
        self.assertEqual(self.pipeline.status, "pending_canary")
        self.assertEqual(self.pipeline.rollback_state, "not_available")
        self.assertIsNone(self.pipeline.canary_evaluation)

    def test_canary_pass_promotes_release_and_exposes_rollback_token(self) -> None:
        promoted = self.manager.evaluate_canary(
            self.pipeline.pipeline_id,
            CanaryObservation(
                request_count=2000,
                error_count=10,
                p95_latency_ms=180.0,
                metadata={"window": "5m"},
            ),
        )

        self.assertEqual(promoted.status, "promoted")
        self.assertIsNotNone(promoted.canary_evaluation)
        self.assertEqual(promoted.canary_evaluation.status, "passed")
        self.assertEqual(promoted.rollback_state, "available")
        self.assertTrue((promoted.rollback_token or "").startswith("rollback-"))

    def test_canary_failure_marks_pipeline_for_rollback(self) -> None:
        failed = self.manager.evaluate_canary(
            self.pipeline.pipeline_id,
            CanaryObservation(
                request_count=1000,
                error_count=90,
                p95_latency_ms=420.0,
                metadata={"window": "5m"},
            ),
        )

        self.assertEqual(failed.status, "canary_failed")
        self.assertEqual(failed.rollback_state, "available")
        self.assertIsNotNone(failed.canary_evaluation)
        self.assertEqual(failed.canary_evaluation.status, "failed")
        self.assertTrue(any("Error rate breached" in reason for reason in failed.canary_evaluation.reasons))

    def test_execute_rollback_requires_valid_token(self) -> None:
        promoted = self.manager.evaluate_canary(
            self.pipeline.pipeline_id,
            CanaryObservation(
                request_count=1500,
                error_count=12,
                p95_latency_ms=190.0,
                metadata={},
            ),
        )

        with self.assertRaises(ReleasePipelineError):
            self.manager.execute_rollback(
                pipeline_id=promoted.pipeline_id,
                rollback_token="rollback-invalid-token",
                reason="rollback requested",
                actor_role="oncall_engineer",
            )

    def test_execute_rollback_transitions_pipeline_to_rolled_back(self) -> None:
        failed = self.manager.evaluate_canary(
            self.pipeline.pipeline_id,
            CanaryObservation(
                request_count=1100,
                error_count=70,
                p95_latency_ms=260.0,
                metadata={"window": "5m"},
            ),
        )

        rolled_back = self.manager.execute_rollback(
            pipeline_id=failed.pipeline_id,
            rollback_token=failed.rollback_token or "",
            reason="canary did not meet reliability thresholds",
            actor_role="incident_commander",
        )

        self.assertEqual(rolled_back.status, "rolled_back")
        self.assertEqual(rolled_back.rollback_state, "executed")
        self.assertEqual(rolled_back.rollback_reason, "canary did not meet reliability thresholds")
        self.assertIsNotNone(rolled_back.rolled_back_at)

    def test_cannot_evaluate_canary_twice(self) -> None:
        self.manager.evaluate_canary(
            self.pipeline.pipeline_id,
            CanaryObservation(
                request_count=1500,
                error_count=10,
                p95_latency_ms=185.0,
                metadata={},
            ),
        )

        with self.assertRaises(ReleasePipelineError):
            self.manager.evaluate_canary(
                self.pipeline.pipeline_id,
                CanaryObservation(
                    request_count=1700,
                    error_count=8,
                    p95_latency_ms=170.0,
                    metadata={},
                ),
            )

    def test_pipeline_manifest_is_deterministic(self) -> None:
        promoted = self.manager.evaluate_canary(
            self.pipeline.pipeline_id,
            CanaryObservation(
                request_count=1800,
                error_count=9,
                p95_latency_ms=175.0,
                metadata={},
            ),
        )

        first = promoted.to_manifest()
        second = promoted.to_manifest()
        self.assertEqual(first, second)

    def test_validate_policy_rejects_invalid_thresholds(self) -> None:
        invalid = replace(
            build_default_canary_threshold_policy(),
            max_error_rate=1.2,
        )

        with self.assertRaises(ReleasePipelineError):
            validate_canary_threshold_policy(invalid)

    def test_create_pipeline_rejects_invalid_rollout_percentage(self) -> None:
        with self.assertRaises(ReleasePipelineError):
            self.manager.create_pipeline(
                release_id="release-2026-04-21",
                previous_release_id="release-2026-04-20",
                canary_percentage=0,
            )


class CanaryPolicyValidationTests(unittest.TestCase):
    def test_default_policy_validates(self) -> None:
        policy = build_default_canary_threshold_policy()
        validate_canary_threshold_policy(policy)
        self.assertIsInstance(policy, CanaryThresholdPolicy)


if __name__ == "__main__":
    unittest.main()
