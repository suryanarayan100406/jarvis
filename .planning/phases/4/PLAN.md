# Phase 4 Plan - Control Plane for Laptop and Servers

## Phase Goal
Implement a secure control plane that allows FRIDAY to operate across the local machine and authorized remote servers with strict permission boundaries.

## Dependencies
1. Phase 1 and Phase 2 completed and verified.
2. Runtime policy enforcement active for all tool actions.

## Scope
1. Host inventory and connector lifecycle management.
2. Local and remote command adapters.
3. Permission-scoped operation templates.
4. Safe rollout, dry-run, and rollback capabilities.
5. Cross-host execution orchestration and result aggregation.

## Task Breakdown
1. P4-T1: Implement host inventory service with labels, roles, and trust levels.
2. P4-T2: Implement connector manager for local and remote transport adapters.
3. P4-T3: Implement SSH-based remote connector with key isolation.
4. P4-T4: Implement scoped command template library per host role.
5. P4-T5: Implement policy overlay for host, command, and operator scope.
6. P4-T6: Implement dry-run mode for potentially destructive operations.
7. P4-T7: Implement rollback actions for service restart and deploy routines.
8. P4-T8: Implement parallel orchestration with bounded concurrency controls.
9. P4-T9: Implement structured result aggregation and host-by-host reporting.
10. P4-T10: Implement connector health checks and retry policies.
11. P4-T11: Add integration tests for multi-host workflows.
12. P4-T12: Add adversarial tests for permission leakage and connector misuse.

## Deliverables
1. Host inventory and connector management modules.
2. Policy-aware remote execution framework.
3. Safe execution toolkit with dry-run and rollback primitives.
4. Multi-host operation report pipeline.
5. Security and reliability test coverage for control-plane actions.

## Verification Plan
1. Unit tests:
   - Host scope resolution.
   - Command allowlist matching.
   - Retry and backoff behavior.
2. Integration tests:
   - Multi-host command fan-out and result aggregation.
   - Partial failure handling and rollback orchestration.
   - Dry-run verification before execution.
3. Adversarial tests:
   - Cross-host privilege escalation attempts.
   - Unauthorized command injection.
   - Stale credential replay attempts.

## Exit Criteria
1. Approved commands execute only on authorized hosts.
2. Dry-run and rollback workflows are reliable.
3. Multi-host operations produce complete and auditable reports.
4. Security tests show no permission boundary bypass.

## Risks and Mitigations
1. Risk: privilege creep from shared connector credentials.
   Mitigation: per-host credentials and strict least privilege roles.
2. Risk: cascading failures in parallel fan-out.
   Mitigation: bounded concurrency and circuit-breaker policies.
3. Risk: operator error on destructive commands.
   Mitigation: policy gating and mandatory dry-run for high-risk actions.

## Definition of Done
Phase is done when FRIDAY can safely orchestrate authorized actions across local and remote systems with clear boundaries and rollback support.
