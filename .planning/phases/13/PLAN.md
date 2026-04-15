# Phase 13 Plan - Governance Artifact Contracts and Templates

## Phase Goal
Define and enforce standard artifact contracts for phase summary, verification, and validation records so milestone closure can be machine-audited.

## Dependencies
1. Phase 12 completed and verified.
2. Milestone closeout audit available at `.planning/v0-MILESTONE-AUDIT.md`.
3. Planning archive snapshots available under `.planning/milestones/`.

## Scope
1. Artifact schema definitions for summary, verification, and validation files.
2. Canonical markdown templates for authoring governance artifacts.
3. Parser and lint validation baseline for required frontmatter and fields.
4. Authoring guidance and migration checklist for future phases.

## Task Breakdown
1. P13-T1: Define frontmatter schema contract for `*-SUMMARY.md` artifacts.
2. P13-T2: Define frontmatter schema contract for `*-VERIFICATION.md` artifacts.
3. P13-T3: Define frontmatter schema contract for `*-VALIDATION.md` artifacts.
4. P13-T4: Create canonical markdown templates for summary, verification, and validation artifacts.
5. P13-T5: Implement schema parser utilities for planning artifact contracts.
6. P13-T6: Implement artifact lint command for schema and required-field validation.
7. P13-T7: Integrate artifact lint checks into phase completion workflow hooks.
8. P13-T8: Add sample governance artifacts for a pilot phase to validate templates.
9. P13-T9: Add positive parser tests for valid frontmatter permutations.
10. P13-T10: Add negative parser tests for missing or malformed fields.
11. P13-T11: Document artifact authoring workflow and contribution guardrails.
12. P13-T12: Publish migration checklist for artifact adoption across existing phases.

## Deliverables
1. Governance artifact schema specification.
2. Reusable summary, verification, and validation markdown templates.
3. Artifact lint and schema parser baseline with test coverage.
4. Operator-facing artifact authoring and migration guide.

## Verification Plan
1. Unit tests:
   - Schema parser validation for required and optional fields.
   - Deterministic normalization of frontmatter and section keys.
2. Integration tests:
   - Artifact lint command across valid and invalid fixture files.
   - Workflow hook execution with lint pass and fail outcomes.
3. Regression tests:
   - Backward compatibility checks for existing planning markdown content.
   - Repeated lint execution determinism checks.
4. Acceptance tests:
   - Governance artifact templates accepted by planning maintainers.
   - Lint command rejects malformed artifacts with actionable errors.

## Exit Criteria
1. All three artifact classes have locked schema contracts.
2. Artifact lint baseline is implemented and test-verified.
3. Templates and migration guidance are published for Phase 14 backfill.
4. No unresolved schema ambiguity remains in governance artifact contracts.

## Risks and Mitigations
1. Risk: schema overfitting to current milestone workflows.
   Mitigation: include extensibility fields and versioned schema metadata.
2. Risk: lint friction slows normal phase execution.
   Mitigation: clear error messages and fast lint runtime budget.
3. Risk: inconsistent author adoption.
   Mitigation: canonical templates and strict completion gates.

## Definition of Done
Phase is done when governance artifacts are formally standardized, lintable, and ready for project-wide rollout.
