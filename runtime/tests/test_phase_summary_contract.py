"""Tests for P13-T1 phase summary artifact contract validation."""

from __future__ import annotations

import unittest

from runtime.validation import (
    PhaseSummaryContractError,
    build_phase_summary_record,
    required_summary_fields,
    validate_phase_summary_artifact,
)


class PhaseSummaryContractTests(unittest.TestCase):
    def test_validate_phase_summary_artifact_accepts_valid_frontmatter(self) -> None:
        record = validate_phase_summary_artifact(
            """
---
phase: 13
plan: P13-T1
title: Summary artifact schema baseline
status: completed
requirements_completed: [FR-019, FR-020]
evidence: [.planning/phases/13/PLAN.md, runtime/tests/test_phase_summary_contract.py]
generated_at: 2026-04-15T23:10:00Z
---
# Summary
Validated baseline summary contract.
""".strip()
        )

        self.assertEqual(record.phase_id, "13")
        self.assertEqual(record.plan_id, "P13-T1")
        self.assertEqual(record.status, "completed")
        self.assertEqual(record.requirements_completed, ("FR-019", "FR-020"))
        self.assertEqual(len(record.evidence_refs), 2)

    def test_build_phase_summary_record_rejects_invalid_status(self) -> None:
        with self.assertRaises(PhaseSummaryContractError):
            build_phase_summary_record(
                {
                    "phase_id": "13",
                    "plan_id": "P13-T1",
                    "title": "Bad status",
                    "status": "done",
                    "requirements_completed": ["FR-019"],
                    "evidence_refs": ["runtime/tests/test_phase_summary_contract.py"],
                    "generated_at": "2026-04-15T23:10:00Z",
                }
            )

    def test_validate_phase_summary_artifact_requires_frontmatter_markers(self) -> None:
        with self.assertRaises(PhaseSummaryContractError):
            validate_phase_summary_artifact("phase: 13\nstatus: completed")

    def test_build_phase_summary_record_requires_generated_at(self) -> None:
        with self.assertRaises(PhaseSummaryContractError):
            build_phase_summary_record(
                {
                    "phase_id": "13",
                    "plan_id": "P13-T1",
                    "title": "Missing generated_at",
                    "status": "completed",
                    "requirements_completed": ["FR-019"],
                    "evidence_refs": ["runtime/tests/test_phase_summary_contract.py"],
                }
            )

    def test_required_fields_stable(self) -> None:
        self.assertEqual(
            required_summary_fields(),
            (
                "phase_id",
                "plan_id",
                "title",
                "status",
                "requirements_completed",
                "evidence_refs",
                "generated_at",
            ),
        )

    def test_manifest_is_deterministic(self) -> None:
        record = build_phase_summary_record(
            {
                "phase_id": "13",
                "plan_id": "P13-T1",
                "title": "Manifest deterministic",
                "status": "completed",
                "requirements_completed": ["FR-019", "FR-020"],
                "evidence_refs": [
                    ".planning/phases/13/PLAN.md",
                    "runtime/tests/test_phase_summary_contract.py",
                ],
                "generated_at": "2026-04-15T23:10:00Z",
                "metadata": {"owner": "planning", "priority": "high"},
            }
        )

        first = record.to_manifest()
        second = record.to_manifest()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
