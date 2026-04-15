"""Tests for P11-T6 disaster-recovery runbook orchestration."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.orchestration import (
    DisasterRecoveryRunbookError,
    DisasterRecoveryRunbookManager,
    RecoveryStepObservation,
    build_default_disaster_recovery_runbook,
    validate_disaster_recovery_runbook,
)


class DisasterRecoveryRunbookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runbook = build_default_disaster_recovery_runbook()
        self.manager = DisasterRecoveryRunbookManager()

    def test_default_runbook_is_valid_and_contains_required_subsystems(self) -> None:
        validate_disaster_recovery_runbook(self.runbook)

        subsystem_ids = {target.subsystem_id for target in self.runbook.recovery_windows}
        self.assertTrue({"orchestration", "memory", "configuration"}.issubset(subsystem_ids))

    def test_evaluate_drill_completes_when_windows_are_met(self) -> None:
        observations = self._build_success_observations()

        result = self.manager.evaluate_drill(self.runbook, list(observations.values()))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.windows_breached_count, 0)
        self.assertEqual(result.windows_met_count, len(self.runbook.recovery_windows))
        self.assertEqual(result.failed_steps, 0)

    def test_evaluate_drill_fails_on_required_rto_breach(self) -> None:
        observations = self._build_success_observations()
        target = self.runbook.get_target("orchestration_failover")
        observations[target.step_id] = replace(
            observations[target.step_id],
            actual_duration_minutes=target.target_rto_minutes + 4.0,
        )

        result = self.manager.evaluate_drill(self.runbook, list(observations.values()), strict=True)

        self.assertEqual(result.status, "failed")
        self.assertGreaterEqual(result.windows_breached_count, 1)
        failed = next(item for item in result.evaluations if item.step_id == target.step_id)
        self.assertEqual(failed.status, "failed")
        self.assertIn("RTO window breached", failed.reason or "")

    def test_evaluate_drill_degrades_for_optional_window_breach(self) -> None:
        optionalized_targets = []
        for target in self.runbook.recovery_windows:
            if target.step_id == "security_verification":
                optionalized_targets.append(replace(target, required=False))
            else:
                optionalized_targets.append(target)
        runbook = replace(self.runbook, recovery_windows=tuple(optionalized_targets))

        observations = self._build_success_observations(runbook=runbook)
        optional_target = runbook.get_target("security_verification")
        observations[optional_target.step_id] = replace(
            observations[optional_target.step_id],
            observed_data_loss_minutes=optional_target.target_rpo_minutes + 1.0,
        )

        result = self.manager.evaluate_drill(runbook, list(observations.values()), strict=False)

        self.assertEqual(result.status, "degraded")
        self.assertFalse(any(item.step_id == optional_target.step_id and item.status == "skipped" for item in result.evaluations))
        breached = next(item for item in result.evaluations if item.step_id == optional_target.step_id)
        self.assertFalse(breached.rpo_met)

    def test_evaluate_drill_marks_missing_required_observation_as_failed(self) -> None:
        observations = self._build_success_observations()
        observations.pop("memory_restore")

        result = self.manager.evaluate_drill(self.runbook, list(observations.values()), strict=False)

        self.assertEqual(result.status, "failed")
        missing = next(item for item in result.evaluations if item.step_id == "memory_restore")
        self.assertEqual(missing.status, "failed")
        self.assertIn("Missing observation", missing.reason or "")

    def test_validate_runbook_rejects_missing_required_subsystem(self) -> None:
        reduced_targets = tuple(
            target
            for target in self.runbook.recovery_windows
            if target.subsystem_id != "configuration"
        )
        invalid = replace(self.runbook, recovery_windows=reduced_targets)

        with self.assertRaises(DisasterRecoveryRunbookError):
            validate_disaster_recovery_runbook(invalid)

    def test_drill_manifest_is_deterministic(self) -> None:
        observations = self._build_success_observations()
        result = self.manager.evaluate_drill(self.runbook, list(observations.values()))

        first = result.to_manifest()
        second = result.to_manifest()
        self.assertEqual(first, second)

    def _build_success_observations(self, *, runbook=None) -> dict[str, RecoveryStepObservation]:
        selected_runbook = runbook or self.runbook
        observations: dict[str, RecoveryStepObservation] = {}
        for target in selected_runbook.recovery_windows:
            observations[target.step_id] = RecoveryStepObservation(
                step_id=target.step_id,
                actual_duration_minutes=max(0.1, target.target_rto_minutes - 1.0),
                observed_data_loss_minutes=max(0.0, target.target_rpo_minutes - 0.5),
                status="completed",
                details=None,
                metadata={"source": "simulation"},
            )
        return observations


if __name__ == "__main__":
    unittest.main()
