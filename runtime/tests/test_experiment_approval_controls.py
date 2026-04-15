"""Tests for P10-T6 experiment approval and rollback controls."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.moonshot import (
    ExperimentApprovalControlError,
    ExperimentApprovalController,
    ExperimentApprovalPolicy,
    SelfImprovementExperimentProposal,
    SelfImprovementSandbox,
    build_default_benchmark_taxonomy,
    validate_experiment_approval_policy,
)


class ExperimentApprovalControlsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = SelfImprovementSandbox(build_default_benchmark_taxonomy())

        self.high_risk_experiment = self.sandbox.register_experiment(
            SelfImprovementExperimentProposal(
                experiment_id="adaptive_planner_high",
                title="Adaptive Planner High",
                hypothesis="High-risk planner adaptation can improve outage recovery quality.",
                target_capability_ids=("contingency_replanning", "long_horizon_tracking"),
                proposed_tool_ids=("benchmark_harness", "scenario_builder", "result_reporter"),
                risk_tier="high",
                metadata={},
            )
        )
        self.low_risk_experiment = self.sandbox.register_experiment(
            SelfImprovementExperimentProposal(
                experiment_id="retrieval_refine_low",
                title="Retrieval Refinement Low",
                hypothesis="Small retrieval tuning improves citation precision.",
                target_capability_ids=("retrieval_grounding",),
                proposed_tool_ids=("benchmark_harness", "result_reporter"),
                risk_tier="low",
                metadata={},
            )
        )

        self.controller = ExperimentApprovalController(self.sandbox)

    def test_create_request_requires_completed_run(self) -> None:
        pending = self.sandbox.create_run(
            self.high_risk_experiment.experiment_id,
            deterministic_seed=11,
            requested_tool_ids=["benchmark_harness"],
        )

        with self.assertRaises(ExperimentApprovalControlError):
            self.controller.create_approval_request(
                pending.run_id,
                requested_by="owner-a",
                summary="promote pending run",
            )

    def test_high_risk_requires_dual_approval_with_primary_user(self) -> None:
        completed = self._create_completed_run(self.high_risk_experiment.experiment_id, seed=21)

        request = self.controller.create_approval_request(
            completed.run_id,
            requested_by="owner-a",
            summary="promote high-risk planner update",
        )

        request = self.controller.add_approval(
            request.request_id,
            approver_id="ops-reviewer",
            approver_role="authorized_operator",
            note="Operational checks passed.",
        )
        self.assertEqual(request.status, "pending")

        with self.assertRaises(ExperimentApprovalControlError):
            self.controller.promote_request(
                request.request_id,
                transition_token=request.active_transition_token or "",
                target_id="planner-service",
                previous_version="v1.2.0",
                promoted_version="v1.3.0",
            )

        request = self.controller.add_approval(
            request.request_id,
            approver_id="primary-reviewer",
            approver_role="primary_user",
            note="Primary approval granted.",
        )
        self.assertEqual(request.status, "approved")

        promotion = self.controller.promote_request(
            request.request_id,
            transition_token=request.active_transition_token or "",
            target_id="planner-service",
            previous_version="v1.2.0",
            promoted_version="v1.3.0",
        )

        self.assertEqual(promotion.rollback_state, "available")
        self.assertEqual(self.controller.get_request(request.request_id).status, "promoted")

    def test_high_risk_blocks_requester_self_approval(self) -> None:
        completed = self._create_completed_run(self.high_risk_experiment.experiment_id, seed=31)

        request = self.controller.create_approval_request(
            completed.run_id,
            requested_by="owner-a",
            summary="high-risk request",
        )

        with self.assertRaises(ExperimentApprovalControlError):
            self.controller.add_approval(
                request.request_id,
                approver_id="owner-a",
                approver_role="primary_user",
            )

    def test_low_risk_single_approval_can_promote(self) -> None:
        completed = self._create_completed_run(self.low_risk_experiment.experiment_id, seed=41)

        request = self.controller.create_approval_request(
            completed.run_id,
            requested_by="owner-low",
            summary="low-risk retrieval promotion",
        )

        request = self.controller.add_approval(
            request.request_id,
            approver_id="owner-low",
            approver_role="primary_user",
            note="Self-approval for low risk.",
        )
        self.assertEqual(request.status, "approved")

        promotion = self.controller.promote_request(
            request.request_id,
            transition_token=request.active_transition_token or "",
            target_id="retrieval-service",
            previous_version="v0.9.0",
            promoted_version="v0.9.1",
        )
        self.assertEqual(promotion.target_id, "retrieval-service")

    def test_reject_request_prevents_promotion(self) -> None:
        completed = self._create_completed_run(self.low_risk_experiment.experiment_id, seed=51)

        request = self.controller.create_approval_request(
            completed.run_id,
            requested_by="owner-low",
            summary="candidate rejected",
        )

        request = self.controller.reject_request(
            request.request_id,
            reviewer_id="ops-review",
            reviewer_role="authorized_operator",
            reason="validation warning unresolved",
        )
        self.assertEqual(request.status, "rejected")

        with self.assertRaises(ExperimentApprovalControlError):
            self.controller.promote_request(
                request.request_id,
                transition_token="invalid",
                target_id="retrieval-service",
                previous_version="v0.9.0",
                promoted_version="v0.9.1",
            )

    def test_rollback_token_single_use(self) -> None:
        completed = self._create_completed_run(self.low_risk_experiment.experiment_id, seed=61)

        request = self.controller.create_approval_request(
            completed.run_id,
            requested_by="owner-low",
            summary="low-risk rollback check",
        )
        request = self.controller.add_approval(
            request.request_id,
            approver_id="owner-low",
            approver_role="primary_user",
        )
        promotion = self.controller.promote_request(
            request.request_id,
            transition_token=request.active_transition_token or "",
            target_id="retrieval-service",
            previous_version="v0.9.0",
            promoted_version="v1.0.0",
        )

        rolled_back = self.controller.execute_rollback(
            promotion.promotion_id,
            rollback_token=promotion.rollback_token,
            actor_role="authorized_operator",
            reason="post-promotion drift detected",
        )
        self.assertEqual(rolled_back.rollback_state, "executed")

        with self.assertRaises(ExperimentApprovalControlError):
            self.controller.execute_rollback(
                promotion.promotion_id,
                rollback_token=promotion.rollback_token,
                actor_role="authorized_operator",
                reason="second rollback must fail",
            )

    def test_policy_validation_rejects_missing_risk_rule(self) -> None:
        policy = self.controller.policy
        invalid_policy = ExperimentApprovalPolicy(
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            rules=tuple(rule for rule in policy.rules if rule.risk_tier != "critical"),
            metadata=dict(policy.metadata),
        )

        with self.assertRaises(ExperimentApprovalControlError):
            validate_experiment_approval_policy(invalid_policy)

    def _create_completed_run(self, experiment_id: str, *, seed: int):
        run = self.sandbox.create_run(
            experiment_id,
            deterministic_seed=seed,
            requested_tool_ids=["benchmark_harness", "result_reporter"],
        )
        run = self.sandbox.start_run(run.run_id, transition_token=run.active_token or "")
        run = self.sandbox.complete_run(
            run.run_id,
            transition_token=run.active_token or "",
            outcome="completed",
            artifacts={"score": 0.77, "seed": seed},
        )
        return run


if __name__ == "__main__":
    unittest.main()
