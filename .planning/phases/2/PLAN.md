# Phase 2 Plan - Core Runtime and Tooling

## Phase Goal
Build the local orchestration runtime that can accept goals, create plans, execute tool calls, validate outcomes, and report results with deterministic behavior.

## Dependencies
1. Phase 1 completed and verified.
2. Tool contract and policy engine from Phase 1 available as reusable modules.

## Scope
1. Runtime core service and process lifecycle.
2. Planner and executor interface contracts.
3. Tool registry and capability metadata.
4. Local state store and run history.
5. Run replay and reporting API.
6. Developer CLI for task submission and inspection.

## Task Breakdown
1. P2-T1: Create runtime module boundaries for planner, executor, validator, and reporter.
2. P2-T2: Implement run coordinator with deterministic stage transitions.
3. P2-T3: Implement planner interface adapter and deterministic plan serialization.
4. P2-T4: Implement executor engine with timeout, retry, and cancellation hooks.
5. P2-T5: Implement validator stage with policy-aware result checks.
6. P2-T6: Implement reporter stage with summary and artifact references.
7. P2-T7: Implement tool registry with manifest loading and signature checks.
8. P2-T8: Implement local run store with migrations and event indexing.
9. P2-T9: Implement run replay endpoint for debugging and audit.
10. P2-T10: Build CLI commands for submit, status, stop, and replay.
11. P2-T11: Add integration tests for end-to-end orchestration flows.
12. P2-T12: Add performance tests for startup and plan latency budgets.

## Deliverables
1. Runtime orchestration service with complete stage pipeline.
2. Tool registry and manifest verification package.
3. Local persistence layer for run state and events.
4. CLI interface for operator workflows.
5. Integration and performance test suite for core runtime.

## Verification Plan
1. Unit tests:
   - Stage transition correctness.
   - Planner serialization determinism.
   - Executor retry and cancellation behavior.
2. Integration tests:
   - Full plan to execute to validate to report flow.
   - Policy deny propagation through runtime.
   - Replay of historical runs with matching artifacts.
3. Adversarial tests:
   - Corrupted tool manifests.
   - Invalid stage transition attempts.
   - Executor timeout abuse scenarios.
4. Performance checks:
   - Runtime startup under target threshold.
   - Task acknowledgement under target threshold.

## Exit Criteria
1. End-to-end orchestration flow runs deterministically.
2. Tool registry enforces manifest integrity checks.
3. Run state and replay are reliable and queryable.
4. Performance targets for startup and acknowledgement are met.

## Risks and Mitigations
1. Risk: non-deterministic planning output.
   Mitigation: deterministic serialization and seed control.
2. Risk: stage deadlocks under cancellation.
   Mitigation: watchdogs and transition timeout guards.
3. Risk: registry bypass through malformed manifests.
   Mitigation: strict schema plus signature verification.

## Definition of Done
Phase is done when the runtime can reliably orchestrate policy-compliant, replayable task execution from submission through final report.
