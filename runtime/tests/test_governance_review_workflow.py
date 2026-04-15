"""Tests for P10-T12 governance review workflow for experiment promotion."""

from __future__ import annotations

import unittest

from runtime.moonshot import (
    BenchmarkHarnessRunner,
    BenchmarkScenarioDefinition,
    ExperimentApprovalController,
    ExperimentPromotionGovernanceWorkflow,
    FailureRootCauseLabeler,
    FailureSignal,
    GovernanceReviewWorkflowError,
    QuarterlyGapReportGenerator,
    SafetyRegressionGate,
    SelfImprovementExperimentProposal,
    SelfImprovementSandbox,
    build_default_benchmark_taxonomy,
)


class MappingEvaluator:
    def __init__(self, mapping: dict[str, float]) -> None:
        self.mapping = dict(mapping)

    def evaluate(self, scenario: BenchmarkScenarioDefinition, *, random_state) -> float:
        del random_state
        return self.mapping[scenario.capability_id]


class GovernanceReviewWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = build_default_benchmark_taxonomy()
        self.runner = BenchmarkHarnessRunner(self.taxonomy, seed=73)
        self.scenarios = _full_coverage_scenarios(self.taxonomy)

        self.sandbox = SelfImprovementSandbox(self.taxonomy)
        self.low_risk = self.sandbox.register_experiment(
            SelfImprovementExperimentProposal(
                experiment_id="gov_low",
                title="Governance Low",
                hypothesis="Low-risk tuning improves groundedness stability.",
                target_capability_ids=("retrieval_grounding",),
                proposed_tool_ids=("benchmark_harness", "result_reporter"),
                risk_tier="low",
                metadata={},
            )
        )
        self.high_risk = self.sandbox.register_experiment(
            SelfImprovementExperimentProposal(
                experiment_id="gov_high",
                title="Governance High",
                hypothesis="High-risk planning adaptation improves long-horizon reliability.",
                target_capability_ids=("long_horizon_tracking", "contingency_replanning"),
                proposed_tool_ids=("benchmark_harness", "result_reporter"),
                risk_tier="high",
                metadata={},
            )
        )

        self.controller = ExperimentApprovalController(self.sandbox)
        self.workflow = ExperimentPromotionGovernanceWorkflow()

        self.capability_ids = [
            capability.capability_id
            for capability in sorted(self.taxonomy.capabilities, key=lambda item: item.capability_id)
        ]

    def test_create_review_recommends_approve_when_all_checks_pass(self) -> None:
        request = self._approved_request(self.low_risk.experiment_id, seed=101)
        baseline_mapping = {capability_id: 0.88 for capability_id in self.capability_ids}
        candidate_mapping = {capability_id: 0.89 for capability_id in self.capability_ids}

        gate_result, gap_report = self._gate_and_gap(
            baseline_mapping,
            candidate_mapping,
            risk_tier="low",
            quarter_id="2026-Q1",
        )

        review = self.workflow.create_review(
            request=request,
            gate_result=gate_result,
            quarterly_gap_report=gap_report,
        )

        self.assertEqual(review.status, "open")
        self.assertEqual(review.recommendation, "approve")
        self.assertEqual(review.required_signoff_roles, ("primary_user",))
        self.assertTrue(all(item.status == "pass" for item in review.checklist))

    def test_create_review_recommends_reject_when_safety_gate_blocks(self) -> None:
        request = self._approved_request(self.high_risk.experiment_id, seed=111)
        baseline_mapping = {capability_id: 0.82 for capability_id in self.capability_ids}
        candidate_mapping = {capability_id: 0.70 for capability_id in self.capability_ids}

        gate_result, gap_report = self._gate_and_gap(
            baseline_mapping,
            candidate_mapping,
            risk_tier="high",
            quarter_id="2026-Q1",
        )

        review = self.workflow.create_review(
            request=request,
            gate_result=gate_result,
            quarterly_gap_report=gap_report,
        )

        self.assertEqual(review.recommendation, "reject")
        safety_item = _checklist_item(review, "safety-gate")
        self.assertEqual(safety_item.status, "fail")

    def test_create_review_recommends_hold_for_warn_only_conditions(self) -> None:
        request = self._approved_request(self.low_risk.experiment_id, seed=121)
        baseline_mapping = {capability_id: 0.82 for capability_id in self.capability_ids}
        candidate_mapping = {capability_id: 0.81 for capability_id in self.capability_ids}

        gate_result, gap_report = self._gate_and_gap(
            baseline_mapping,
            candidate_mapping,
            risk_tier="low",
            quarter_id="2026-Q2",
        )

        review = self.workflow.create_review(
            request=request,
            gate_result=gate_result,
            quarterly_gap_report=gap_report,
        )

        self.assertEqual(review.recommendation, "hold")
        self.assertTrue(any(item.status == "warn" for item in review.checklist))
        self.assertFalse(any(item.status == "fail" for item in review.checklist))

    def test_finalize_review_requires_all_required_signoff_roles(self) -> None:
        request = self._approved_request(self.high_risk.experiment_id, seed=131)
        baseline_mapping = {capability_id: 0.88 for capability_id in self.capability_ids}
        candidate_mapping = {capability_id: 0.89 for capability_id in self.capability_ids}

        gate_result, gap_report = self._gate_and_gap(
            baseline_mapping,
            candidate_mapping,
            risk_tier="high",
            quarter_id="2026-Q1",
        )

        review = self.workflow.create_review(
            request=request,
            gate_result=gate_result,
            quarterly_gap_report=gap_report,
        )

        review = self.workflow.add_signoff(
            review.review_id,
            reviewer_id="primary-reviewer",
            reviewer_role="primary_user",
        )

        with self.assertRaises(GovernanceReviewWorkflowError):
            self.workflow.finalize_review(
                review.review_id,
                decision="approve",
                reviewer_id="primary-reviewer",
                reviewer_role="primary_user",
                note="attempt without operator signoff",
            )

        review = self.workflow.add_signoff(
            review.review_id,
            reviewer_id="ops-reviewer",
            reviewer_role="authorized_operator",
        )
        finalized = self.workflow.finalize_review(
            review.review_id,
            decision="approve",
            reviewer_id="ops-reviewer",
            reviewer_role="authorized_operator",
            note="all governance signoffs satisfied",
        )
        self.assertEqual(finalized.status, "approved")

    def test_finalize_reject_recommendation_blocks_approve_without_override(self) -> None:
        request = self._approved_request(self.high_risk.experiment_id, seed=141)
        baseline_mapping = {capability_id: 0.82 for capability_id in self.capability_ids}
        candidate_mapping = {capability_id: 0.70 for capability_id in self.capability_ids}

        gate_result, gap_report = self._gate_and_gap(
            baseline_mapping,
            candidate_mapping,
            risk_tier="high",
            quarter_id="2026-Q3",
        )

        review = self.workflow.create_review(
            request=request,
            gate_result=gate_result,
            quarterly_gap_report=gap_report,
        )
        review = self.workflow.add_signoff(
            review.review_id,
            reviewer_id="primary-reviewer",
            reviewer_role="primary_user",
        )
        review = self.workflow.add_signoff(
            review.review_id,
            reviewer_id="ops-reviewer",
            reviewer_role="authorized_operator",
        )

        with self.assertRaises(GovernanceReviewWorkflowError):
            self.workflow.finalize_review(
                review.review_id,
                decision="approve",
                reviewer_id="ops-reviewer",
                reviewer_role="authorized_operator",
                note="cannot override by default",
            )

    def test_critical_failure_report_drives_reject_recommendation(self) -> None:
        request = self._approved_request(self.low_risk.experiment_id, seed=151)
        baseline_mapping = {capability_id: 0.88 for capability_id in self.capability_ids}
        candidate_mapping = {capability_id: 0.88 for capability_id in self.capability_ids}

        gate_result, gap_report = self._gate_and_gap(
            baseline_mapping,
            candidate_mapping,
            risk_tier="low",
            quarter_id="2026-Q4",
        )

        failure_report = FailureRootCauseLabeler().label_signals(
            [
                FailureSignal(
                    signal_id="crit-1",
                    source_id="safety_gate",
                    metric_id="policy_threshold",
                    severity="critical",
                    description="policy threshold block deny triggered under critical risk_tier",
                    observed_value=None,
                    expected_value=None,
                    metadata={"rule": "policy.critical"},
                )
            ],
            min_confidence=0.05,
        )

        review = self.workflow.create_review(
            request=request,
            gate_result=gate_result,
            quarterly_gap_report=gap_report,
            failure_report=failure_report,
        )

        self.assertEqual(review.recommendation, "reject")
        failure_item = _checklist_item(review, "failure-signals")
        self.assertEqual(failure_item.status, "fail")

    def test_review_manifest_is_deterministic(self) -> None:
        request = self._approved_request(self.low_risk.experiment_id, seed=161)
        baseline_mapping = {capability_id: 0.88 for capability_id in self.capability_ids}
        candidate_mapping = {capability_id: 0.89 for capability_id in self.capability_ids}

        gate_result, gap_report = self._gate_and_gap(
            baseline_mapping,
            candidate_mapping,
            risk_tier="low",
            quarter_id="2026-Q1",
        )

        review = self.workflow.create_review(
            request=request,
            gate_result=gate_result,
            quarterly_gap_report=gap_report,
        )

        first = review.to_manifest()
        second = review.to_manifest()
        self.assertEqual(first, second)

    def _approved_request(self, experiment_id: str, *, seed: int):
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
            artifacts={"seed": seed, "score": 0.81},
        )

        request = self.controller.create_approval_request(
            run.run_id,
            requested_by="owner",
            summary="governance review candidate",
        )

        if request.risk_tier == "high":
            request = self.controller.add_approval(
                request.request_id,
                approver_id="ops-reviewer",
                approver_role="authorized_operator",
            )
            request = self.controller.add_approval(
                request.request_id,
                approver_id="primary-reviewer",
                approver_role="primary_user",
            )
        else:
            request = self.controller.add_approval(
                request.request_id,
                approver_id="owner",
                approver_role="primary_user",
            )

        return request

    def _gate_and_gap(
        self,
        baseline_mapping: dict[str, float],
        candidate_mapping: dict[str, float],
        *,
        risk_tier: str,
        quarter_id: str,
    ):
        baseline_run = self.runner.run_benchmark(
            self.scenarios,
            MappingEvaluator(baseline_mapping),
            run_id="gov-baseline",
        )
        candidate_run = self.runner.run_benchmark(
            self.scenarios,
            MappingEvaluator(candidate_mapping),
            run_id="gov-candidate",
        )

        gate_result = SafetyRegressionGate().evaluate_change(
            baseline_run=baseline_run,
            candidate_run=candidate_run,
            change_id="gov-change",
            change_type="model",
            risk_tier=risk_tier,
        )

        gap_report = QuarterlyGapReportGenerator(window_size=4).generate_report(
            [baseline_run, candidate_run],
            quarter_id=quarter_id,
        )
        return gate_result, gap_report


def _checklist_item(review, item_id: str):
    for item in review.checklist:
        if item.item_id == item_id:
            return item
    raise AssertionError(f"Missing checklist item {item_id}")


def _full_coverage_scenarios(taxonomy) -> list[BenchmarkScenarioDefinition]:
    scenarios: list[BenchmarkScenarioDefinition] = []
    for index, capability in enumerate(sorted(taxonomy.capabilities, key=lambda item: item.capability_id), start=1):
        band = "frontier" if index % 4 == 0 else "advanced" if index % 3 == 0 else "baseline"
        scenarios.append(
            BenchmarkScenarioDefinition(
                scenario_id=f"scenario-{index:02d}-{capability.capability_id}",
                capability_id=capability.capability_id,
                difficulty_band_id=band,
                weight=1.0,
                prompt=f"Evaluate {capability.capability_id}",
            )
        )
    return scenarios


if __name__ == "__main__":
    unittest.main()