# ADR 0001: Security Trust Boundaries

## Status
Accepted

## Context
FRIDAY operates across local systems, remote servers, and optional physical integrations. Without explicit trust boundaries, the assistant could overreach permissions and increase compromise risk.

## Decision
1. Define explicit trust zones:
   - Zone A: Core runtime and policy engine (highest trust).
   - Zone B: Tool adapters and connectors (constrained trust).
   - Zone C: External targets such as hosts, services, and devices (lowest trust).
2. Enforce default-deny across all zone crossings.
3. Require policy evaluation for every action crossing from Zone A to Zone B and Zone C.
4. Require immutable audit event emission for every cross-zone action.
5. Enforce least-privilege credentials for each connector and host scope.

## Consequences
1. Increased implementation complexity due to mandatory policy checks.
2. Lower blast radius when a connector is compromised.
3. Better forensic traceability for all privileged operations.

## Verification Strategy
1. Unit tests: policy enforcement for zone crossings.
2. Integration tests: deny unauthorized cross-zone calls.
3. Adversarial tests: privilege escalation and connector abuse scenarios.
