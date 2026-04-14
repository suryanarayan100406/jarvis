# Phase 1 Plan - Foundation and Governance

## Phase Goal
Create the minimum trusted foundation for FRIDAY: policy enforcement, auditability, runtime control flow, and emergency stop.

## Scope
1. Tool contract and schema.
2. Risk-tier policy engine.
3. Immutable audit event pipeline.
4. Kill-switch and global stop propagation.
5. Baseline run state machine.
6. Identity integrity and anti-injection foundation.

## Task Breakdown
1. P1-T1: Define JSON schemas for tool request/response/error and telemetry envelopes.
2. P1-T2: Implement schema validation middleware and strict failure behavior.
3. P1-T3: Implement policy engine with risk-tier evaluation and decision reason output.
4. P1-T4: Implement audit writer with hash-linked event chain.
5. P1-T5: Implement kill-switch controller and runtime halt hooks.
6. P1-T6: Implement run state machine with deterministic transitions.
7. P1-T7: Add baseline integration tests for policy, audit, and kill-switch behavior.
8. P1-T8: Produce architecture decision records for trust boundaries.
9. P1-T9: Define identity directive schema and form-of-address preference contract.
10. P1-T10: Implement identity override detection and prompt-injection baseline filters.
11. P1-T11: Define boot sequence and status-format protocol contract for runtime integration.

## Deliverables
1. Validated tool contract package.
2. Policy engine module with tests.
3. Audit logging module with tamper detection check.
4. Kill-switch service and integration hooks.
5. State machine module and integration test suite.
6. ADR documents for security and autonomy boundaries.
7. Identity directive contract and override-detection module.
8. Startup and status protocol specification package.

## Verification Plan
1. Unit tests:
   - Policy decisions by risk tier.
   - Schema validation rejection behavior.
   - Kill-switch state transitions.
2. Integration tests:
   - End-to-end run with full audit trail.
   - Mid-run kill-switch interruption.
   - Policy denial propagation to executor.
3. Adversarial tests:
   - Malformed tool payload injection.
   - Unauthorized high-risk command request.
   - Audit log tampering attempt.
   - Identity override prompt attempt.
   - Embedded instruction execution attempt from untrusted document context.

## Exit Criteria
1. All unit and integration tests pass.
2. Adversarial tests demonstrate safe failures.
3. Every tool action produces policy decision plus audit event.
4. Kill-switch halts all active runs deterministically.
5. Identity override attempts are detected, blocked, and logged.

## Risks and Mitigations
1. Risk: policy rules too permissive.
   Mitigation: default deny plus explicit allowlist review.
2. Risk: inconsistent event schema across modules.
   Mitigation: centralized schema package with version pinning.
3. Risk: kill-switch race conditions.
   Mitigation: atomic state flag plus event-bus broadcast acknowledgements.

## Definition of Done
Phase is done when trusted execution controls are demonstrably enforced and tested under normal and adversarial conditions.
