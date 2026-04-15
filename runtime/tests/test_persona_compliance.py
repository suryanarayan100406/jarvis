"""Tests for P12-T4 persona compliance evaluator."""

from __future__ import annotations

import unittest

from runtime.persona import (
    PersonaComplianceError,
    PersonaComplianceEvaluator,
    PersonaComplianceSample,
)


class PersonaComplianceEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = PersonaComplianceEvaluator()

    def test_friday_profile_passes_with_consistent_samples(self) -> None:
        samples = (
            self._sample("s1", "friday", "Boss", "Boss, All systems nominal [confidence:high]", ("persona:friday", "answer-first")),
            self._sample("s2", "friday", "Boss", "Boss, Deploy validated [confidence:medium]", ("persona:friday",)),
            self._sample("s3", "friday", "Boss", "Boss, Monitoring active [confidence:high]", ("persona:friday",)),
        )

        report = self.evaluator.evaluate_profile("friday", samples)

        self.assertEqual(report.status, "pass")
        self.assertEqual(report.fail_count, 0)
        self.assertGreaterEqual(report.compliance_score, 0.95)

    def test_jarvis_profile_warns_on_addressing_drift(self) -> None:
        samples = (
            self._sample("j1", "jarvis", "Sir", "Sir, Telemetry synced [confidence:high]", ("persona:jarvis",)),
            self._sample("j2", "jarvis", "Operator", "Operator, Threats clear [confidence:medium]", ("persona:jarvis",)),
            self._sample("j3", "jarvis", "Sir", "Sir, Backup complete [confidence:high]", ("persona:jarvis",)),
        )

        report = self.evaluator.evaluate_profile("jarvis", samples)

        self.assertEqual(report.status, "warn")
        addressing_check = next(item for item in report.checks if item.check_id == "addressing-consistency")
        self.assertEqual(addressing_check.status, "warn")

    def test_standard_profile_batch_includes_friday_and_jarvis(self) -> None:
        friday_samples = (
            self._sample("f1", "friday", "Boss", "Boss, All green [confidence:high]", ("persona:friday",)),
            self._sample("f2", "friday", "Boss", "Boss, Sync complete [confidence:high]", ("persona:friday",)),
            self._sample("f3", "friday", "Boss", "Boss, Ready [confidence:high]", ("persona:friday",)),
        )
        jarvis_samples = (
            self._sample("j1", "jarvis", "Sir", "Sir, Systems stable [confidence:high]", ("persona:jarvis",)),
            self._sample("j2", "jarvis", "Maam", "Maam, No threats [confidence:high]", ("persona:jarvis",)),
            self._sample("j3", "jarvis", "Sir", "Sir, Standing by [confidence:high]", ("persona:jarvis",)),
        )

        batch = self.evaluator.evaluate_standard_profiles(
            {
                "friday": friday_samples,
                "jarvis": jarvis_samples,
            }
        )

        self.assertEqual(len(batch.reports), 2)
        self.assertEqual({report.profile_id for report in batch.reports}, {"friday", "jarvis"})
        self.assertEqual(batch.overall_status, "pass")

    def test_invalid_profile_sample_mismatch_raises(self) -> None:
        samples = (self._sample("x1", "jarvis", "Sir", "Sir, Ready [confidence:high]", ("persona:jarvis",)),)

        with self.assertRaises(PersonaComplianceError):
            self.evaluator.evaluate_profile("friday", samples)

    def test_report_manifest_is_deterministic(self) -> None:
        samples = (
            self._sample("s1", "friday", "Boss", "Boss, all good [confidence:high]", ("persona:friday",)),
            self._sample("s2", "friday", "Boss", "Boss, checks done [confidence:medium]", ("persona:friday",)),
            self._sample("s3", "friday", "Boss", "Boss, standing by [confidence:high]", ("persona:friday",)),
        )

        report = self.evaluator.evaluate_profile("friday", samples)
        first = report.to_manifest()
        second = report.to_manifest()

        self.assertEqual(first, second)

    @staticmethod
    def _sample(sample_id: str, profile_id: str, addressed_to: str, response_text: str, tags: tuple[str, ...]) -> PersonaComplianceSample:
        return PersonaComplianceSample(
            sample_id=sample_id,
            profile_id=profile_id,
            addressed_to=addressed_to,
            response_text=response_text,
            response_tags=tags,
            mode="standard",
            metadata={},
        )


if __name__ == "__main__":
    unittest.main()
