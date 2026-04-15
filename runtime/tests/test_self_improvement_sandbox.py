"""Tests for P10-T5 self-improvement sandbox isolation controls."""

from __future__ import annotations

import unittest

from runtime.moonshot import (
    SelfImprovementExperimentProposal,
    SelfImprovementSandbox,
    SelfImprovementSandboxError,
    build_default_benchmark_taxonomy,
)


class SelfImprovementSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = build_default_benchmark_taxonomy()
        self.sandbox = SelfImprovementSandbox(self.taxonomy)
        self.proposal = self.sandbox.register_experiment(
            SelfImprovementExperimentProposal(
                experiment_id="planner_feedback_v1",
                title="Planner Feedback Adaptation",
                hypothesis="Structured benchmark feedback improves contingency planning quality.",
                target_capability_ids=("contingency_replanning", "long_horizon_tracking"),
                proposed_tool_ids=("benchmark_harness", "scenario_builder", "result_reporter"),
                risk_tier="medium",
                metadata={"owner": "moonshot-lab"},
            )
        )

    def test_register_experiment_rejects_unknown_capability(self) -> None:
        with self.assertRaises(SelfImprovementSandboxError):
            self.sandbox.register_experiment(
                SelfImprovementExperimentProposal(
                    experiment_id="invalid_caps",
                    title="Invalid",
                    hypothesis="Should fail",
                    target_capability_ids=("missing_capability",),
                    proposed_tool_ids=("benchmark_harness",),
                    risk_tier="low",
                    metadata={},
                )
            )

    def test_create_run_blocks_network_and_secret_access(self) -> None:
        with self.assertRaises(SelfImprovementSandboxError):
            self.sandbox.create_run(
                self.proposal.experiment_id,
                deterministic_seed=11,
                requested_tool_ids=["benchmark_harness"],
                network_access=True,
            )

        with self.assertRaises(SelfImprovementSandboxError):
            self.sandbox.create_run(
                self.proposal.experiment_id,
                deterministic_seed=11,
                requested_tool_ids=["benchmark_harness"],
                secret_access=True,
            )

    def test_create_run_rejects_tool_not_declared_on_experiment(self) -> None:
        with self.assertRaises(SelfImprovementSandboxError):
            self.sandbox.create_run(
                self.proposal.experiment_id,
                deterministic_seed=5,
                requested_tool_ids=["taxonomy_manifest"],
            )

    def test_start_rotates_token_and_old_token_cannot_complete(self) -> None:
        pending = self.sandbox.create_run(
            self.proposal.experiment_id,
            deterministic_seed=23,
            requested_tool_ids=["benchmark_harness", "result_reporter"],
        )
        initial_token = pending.active_token
        self.assertIsNotNone(initial_token)

        running = self.sandbox.start_run(
            pending.run_id,
            transition_token=initial_token or "",
        )
        self.assertEqual(running.status, "running")
        self.assertNotEqual(running.active_token, initial_token)

        with self.assertRaises(SelfImprovementSandboxError):
            self.sandbox.complete_run(
                running.run_id,
                transition_token=initial_token or "",
                outcome="completed",
                artifacts={"score": 0.9},
            )

        completed = self.sandbox.complete_run(
            running.run_id,
            transition_token=running.active_token or "",
            outcome="completed",
            artifacts={"score": 0.9},
        )
        self.assertEqual(completed.status, "completed")
        self.assertIsNone(completed.active_token)

    def test_complete_requires_running_state(self) -> None:
        pending = self.sandbox.create_run(
            self.proposal.experiment_id,
            deterministic_seed=7,
            requested_tool_ids=["scenario_builder"],
        )

        with self.assertRaises(SelfImprovementSandboxError):
            self.sandbox.complete_run(
                pending.run_id,
                transition_token=pending.active_token or "",
                outcome="completed",
                artifacts={"score": 0.4},
            )

    def test_artifact_digest_is_deterministic_for_equivalent_artifacts(self) -> None:
        run_a = self.sandbox.create_run(
            self.proposal.experiment_id,
            deterministic_seed=101,
            requested_tool_ids=["benchmark_harness", "result_reporter"],
        )
        run_a = self.sandbox.start_run(run_a.run_id, transition_token=run_a.active_token or "")
        run_a = self.sandbox.complete_run(
            run_a.run_id,
            transition_token=run_a.active_token or "",
            outcome="completed",
            artifacts={"b": 2, "a": 1},
        )

        run_b = self.sandbox.create_run(
            self.proposal.experiment_id,
            deterministic_seed=101,
            requested_tool_ids=["result_reporter", "benchmark_harness"],
        )
        run_b = self.sandbox.start_run(run_b.run_id, transition_token=run_b.active_token or "")
        run_b = self.sandbox.complete_run(
            run_b.run_id,
            transition_token=run_b.active_token or "",
            outcome="completed",
            artifacts={"a": 1, "b": 2},
        )

        self.assertEqual(run_a.artifact_digest, run_b.artifact_digest)

    def test_list_runs_filters_status(self) -> None:
        run = self.sandbox.create_run(
            self.proposal.experiment_id,
            deterministic_seed=41,
            requested_tool_ids=["scenario_builder"],
        )
        self.sandbox.start_run(run.run_id, transition_token=run.active_token or "")

        pending_runs = self.sandbox.list_runs(status="pending")
        running_runs = self.sandbox.list_runs(status="running")

        self.assertEqual(len(pending_runs), 0)
        self.assertEqual(len(running_runs), 1)


if __name__ == "__main__":
    unittest.main()
