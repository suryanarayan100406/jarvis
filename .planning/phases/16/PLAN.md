# Phase 16 Plan - Requirements Traceability Automation

## Phase Goal
Build automated requirement traceability that links each requirement ID to implementation phases, verification evidence, and final satisfaction status.

## Dependencies
1. Phase 14 backfilled governance artifacts available.
2. Phase 15 audit pipeline baseline available.
3. Requirements expansions FR-021 and FR-022 approved.

## Scope
1. Requirements and artifact parsing into a unified traceability model.
2. Requirement-to-evidence graph construction and status derivation.
3. Markdown and manifest traceability report generation.
4. Orphan and inconsistency detection for release governance.

## Task Breakdown
1. P16-T1: Define requirement ID parsing and mapping specification.
2. P16-T2: Implement REQUIREMENTS parser to extract structured requirement metadata.
3. P16-T3: Implement summary artifact parser for requirements-completed metadata.
4. P16-T4: Implement verification artifact parser for requirement evidence rows.
5. P16-T5: Implement traceability graph merge engine across requirement and phase sources.
6. P16-T6: Implement requirement status classifier (satisfied, partial, unsatisfied, orphaned).
7. P16-T7: Implement markdown traceability table renderer.
8. P16-T8: Implement JSON traceability manifest serializer.
9. P16-T9: Implement anomaly detection for contradictory mappings and missing evidence.
10. P16-T10: Add unit tests for parsing, merge logic, and status classification.
11. P16-T11: Add regression tests for full-project and milestone-scoped traceability generation.
12. P16-T12: Document operator workflow for traceability refresh and review.

## Deliverables
1. Requirement traceability graph generation module.
2. Human-readable and machine-readable traceability reports.
3. Orphan and inconsistency detection with actionable diagnostics.
4. Test coverage for parser, merge, and classification behavior.

## Verification Plan
1. Unit tests:
   - Requirement ID extraction and normalization.
   - Status classification edge cases and orphan detection.
2. Integration tests:
   - End-to-end traceability build from requirements and phase artifacts.
   - Conflict and anomaly reporting behavior.
3. Regression tests:
   - Deterministic output over repeated runs with unchanged inputs.
   - Milestone-scoped filtering behavior over historical artifacts.
4. Acceptance tests:
   - Traceability report includes all requirement IDs.
   - Unsatisfied and orphaned requirements are correctly flagged.

## Exit Criteria
1. Requirement traceability generation is fully automated.
2. Requirement status classification is deterministic and test-verified.
3. Traceability reports are consumable by milestone audit and release gates.
4. No unresolved parser ambiguity remains in requirement mapping workflow.

## Risks and Mitigations
1. Risk: inconsistent requirement ID formatting across documents.
   Mitigation: normalization rules with strict validation and diagnostics.
2. Risk: evidence duplication causes false satisfied status.
   Mitigation: canonical evidence keys and duplicate suppression logic.
3. Risk: operator confusion on anomaly outcomes.
   Mitigation: clear report annotations and documented remediation flow.

## Definition of Done
Phase is done when requirement traceability is automated, accurate, and integrated with governance reporting.
