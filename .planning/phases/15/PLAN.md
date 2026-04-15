# Phase 15 Plan - Automated Milestone Audit Pipeline

## Phase Goal
Implement an automated milestone audit workflow that computes release readiness and governance status from planning artifacts without manual reconstruction.

## Dependencies
1. Phase 13 schema and lint infrastructure completed.
2. Phase 14 historical artifact backfill completed.
3. Governance requirements FR-020 and NFR-009 approved.

## Scope
1. Audit domain model and scoring policy implementation.
2. Artifact ingestion and normalization pipeline.
3. Deterministic markdown and manifest audit report generation.
4. Audit command surface for milestone and project scopes.

## Task Breakdown
1. P15-T1: Define milestone audit domain model and status decision matrix.
2. P15-T2: Implement artifact ingestion pipeline for summary, verification, and validation records.
3. P15-T3: Implement requirement coverage scoring engine.
4. P15-T4: Implement phase completion and verification score aggregation.
5. P15-T5: Implement integration and flow scoring adapters.
6. P15-T6: Implement Nyquist compliance aggregation for milestone status.
7. P15-T7: Implement deterministic digest computation for audit outputs.
8. P15-T8: Implement markdown audit report renderer.
9. P15-T9: Implement machine-readable audit manifest serializer.
10. P15-T10: Add unit tests for scoring, status classification, and rendering.
11. P15-T11: Add integration tests for end-to-end audit generation.
12. P15-T12: Compare automated audit output against v0 manual audit and reconcile deltas.

## Deliverables
1. Automated milestone audit computation module.
2. Deterministic audit markdown and manifest output format.
3. Audit CLI and programmatic interface.
4. Verification suite for scoring and output determinism.

## Verification Plan
1. Unit tests:
   - Status decision matrix and score calculations.
   - Deterministic digest behavior for stable inputs.
2. Integration tests:
   - Full artifact ingestion and milestone audit generation.
   - Mixed pass/tech-debt/gaps scenarios.
3. Regression tests:
   - Repeated audit runs produce identical output digests.
   - Historical milestone data remains parseable and auditable.
4. Acceptance tests:
   - Automated output reproduces expected v0 audit conclusions.
   - Audit runtime meets milestone-scale performance budget.

## Exit Criteria
1. Milestone audit generation is fully automated and deterministic.
2. Audit status classification matches defined governance rules.
3. End-to-end audit tests pass for representative milestone scenarios.
4. Manual audit reconstruction is no longer required for closure decisions.

## Risks and Mitigations
1. Risk: ambiguous scoring rules cause unstable status outputs.
   Mitigation: explicit matrix-based decision policy with golden tests.
2. Risk: artifact inconsistencies break audit ingestion.
   Mitigation: strict lint gate and tolerant parsing with clear errors.
3. Risk: output drift between markdown and manifest.
   Mitigation: shared canonical intermediate model before rendering.

## Definition of Done
Phase is done when milestone audits can be generated deterministically and trusted as release-gate evidence.
