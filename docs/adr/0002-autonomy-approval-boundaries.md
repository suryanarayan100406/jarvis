# ADR 0002: Autonomy Approval Boundaries

## Status
Accepted

## Context
FRIDAY is designed for autonomous workflows, but unrestricted autonomy creates unacceptable operational and safety risks.

## Decision
1. Implement bounded autonomy tiers by risk level:
   - Low: auto-execute.
   - Medium: auto-execute for trusted roles with post-action reporting.
   - High: require explicit approval before execution.
   - Critical: require explicit approval plus confirmation context.
2. Apply stricter gates for production and physical scopes.
3. Allow dry-run mode to lower execution risk where supported.
4. Require confidence-aware escalation when uncertainty exceeds threshold.
5. Block progression when kill-switch is active.

## Consequences
1. High-risk operations have slower execution due to approvals.
2. Safety and trust improve for autonomous flows.
3. Operator workload is reduced for low-risk repetitive tasks.

## Verification Strategy
1. Unit tests: tier decision outcomes by role and environment.
2. Integration tests: escalation workflows for uncertain actions.
3. Audit checks: verify approval evidence in logs for high and critical operations.
