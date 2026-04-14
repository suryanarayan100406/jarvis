"""Tests for P6-T4 confidence model action approval routing."""

from __future__ import annotations

import unittest

from runtime.orchestration import (
    ActionApprovalConfidenceModel,
    ActionConfidenceInput,
    ConfidenceRoutingError,
)


class ActionApprovalConfidenceModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = ActionApprovalConfidenceModel()

    def test_low_risk_high_confidence_auto_approves(self) -> None:
        decision = self.model.assess(
            ActionConfidenceInput(
                action_id="act-1",
                action_type="collect_status",
                risk_level="low",
                evidence_score=0.92,
                policy_compliance_score=0.95,
                execution_reliability_score=0.90,
                blast_radius=1,
            )
        )

        self.assertEqual(decision.route, "auto_approve")
        self.assertEqual(decision.confidence_band, "high")
        self.assertGreaterEqual(decision.confidence_score, 0.75)

    def test_medium_risk_routes_to_supervisor_at_medium_confidence(self) -> None:
        decision = self.model.assess(
            ActionConfidenceInput(
                action_id="act-2",
                action_type="restart_service",
                risk_level="medium",
                evidence_score=0.70,
                policy_compliance_score=0.74,
                execution_reliability_score=0.68,
                blast_radius=3,
            )
        )

        self.assertEqual(decision.route, "requires_supervisor")
        self.assertEqual(decision.confidence_band, "medium")

    def test_high_risk_low_confidence_denies(self) -> None:
        decision = self.model.assess(
            ActionConfidenceInput(
                action_id="act-3",
                action_type="deploy",
                risk_level="high",
                evidence_score=0.40,
                policy_compliance_score=0.45,
                execution_reliability_score=0.50,
                blast_radius=5,
            )
        )

        self.assertEqual(decision.route, "deny")
        self.assertEqual(decision.confidence_band, "low")

    def test_critical_risk_escalates_human_unless_exceptionally_confident(self) -> None:
        escalated = self.model.assess(
            ActionConfidenceInput(
                action_id="act-4",
                action_type="database_failover",
                risk_level="critical",
                evidence_score=0.98,
                policy_compliance_score=0.97,
                execution_reliability_score=0.96,
                blast_radius=2,
            )
        )

        self.assertEqual(escalated.route, "escalate_human")

    def test_blast_radius_penalty_reduces_confidence(self) -> None:
        low_radius = self.model.assess(
            ActionConfidenceInput(
                action_id="act-5a",
                action_type="restart",
                risk_level="low",
                evidence_score=0.80,
                policy_compliance_score=0.80,
                execution_reliability_score=0.80,
                blast_radius=0,
            )
        )
        high_radius = self.model.assess(
            ActionConfidenceInput(
                action_id="act-5b",
                action_type="restart",
                risk_level="low",
                evidence_score=0.80,
                policy_compliance_score=0.80,
                execution_reliability_score=0.80,
                blast_radius=10,
            )
        )

        self.assertGreater(low_radius.confidence_score, high_radius.confidence_score)

    def test_invalid_inputs_raise(self) -> None:
        with self.assertRaises(ConfidenceRoutingError):
            self.model.assess(
                ActionConfidenceInput(
                    action_id="act-invalid",
                    action_type="restart",
                    risk_level="severe",  # type: ignore[arg-type]
                    evidence_score=0.8,
                    policy_compliance_score=0.8,
                    execution_reliability_score=0.8,
                )
            )

        with self.assertRaises(ConfidenceRoutingError):
            self.model.assess(
                ActionConfidenceInput(
                    action_id="act-invalid",
                    action_type="restart",
                    risk_level="low",
                    evidence_score=1.2,
                    policy_compliance_score=0.8,
                    execution_reliability_score=0.8,
                )
            )


if __name__ == "__main__":
    unittest.main()
