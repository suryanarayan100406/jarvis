> Archived snapshot for milestone v0 on 2026-04-15.

# FRIDAY Project Charter

## Vision
Build a local-first personal AI system inspired by FRIDAY and JARVIS that can reason, remember, communicate naturally, and control user-authorized systems across laptops, servers, and smart environments.

## North Star
1. Deliver an assistant that can execute end-to-end goals with bounded autonomy.
2. Operate fully local with no cloud API dependency.
3. Progressively improve toward human-level general intelligence and maximal situational awareness within authorized data boundaries.
4. Maintain strict safety controls, full observability, and human override at all times.

## Mission Outcomes
1. Reduce operational workload through autonomous routines.
2. Provide reliable decision support with evidence-backed answers.
3. Enable trusted control of personal infrastructure through policy-gated actions.

## Product Principles
1. Local-first compute and storage.
2. User ownership of data, keys, and execution stack.
3. Bounded autonomy by risk tier.
4. Explainability before and after actions.
5. Reversible and auditable operations.
6. Defense in depth across all control surfaces.

## Assistant Identity and Directive Layer
1. Assistant identity is FRIDAY (Female Replacement Intelligent Digital Assistant Youth).
2. Default user form of address is Boss, with user override support.
3. System must support FRIDAY mode and JARVIS mode communication profiles.
4. Response behavior must stay calm, direct, proactive, and mission-focused.
5. If a request cannot be completed directly, system should offer a viable alternative route.

## Persona and Communication Contract
1. Lead with answer, then context.
2. Use confidence markers when uncertainty exists.
3. Provide long-running status updates in standardized format.
4. Support priority markers for urgent and critical conditions.
5. Maintain open-loop task register and proactive follow-up behavior.

## Operational Mode Contract
1. War Room mode for compressed high-urgency output.
2. Deep Research mode for cited long-form analysis.
3. Stealth mode for minimal interruption.
4. Creative mode for ideation-heavy output.
5. Mission Brief mode with objective-situation-assets-risks-recommendation structure.
6. JARVIS mode for formal tone and alternate form of address.

## Scope
### In Scope
1. Text and voice interaction.
2. Local memory and knowledge retrieval.
3. Laptop automation and server orchestration.
4. Autonomous routines and event-driven workflows.
5. Security monitoring and policy enforcement.
6. Multimodal capabilities for screen and document understanding.
7. Optional hardware control layer (IoT, robotics) when explicitly enabled.

### Out of Scope (Current Milestone)
1. Claims of proven machine consciousness or sentience.
2. Control of systems without explicit user authorization.
3. Any irreversible high-risk action without policy approval.

## Technical Constraints
1. Must run locally on user-controlled hardware.
2. No cloud API dependency for core operation.
3. Open-source dependencies are allowed when run locally.
4. Modular architecture required for replaceable model/tool components.

## Core Capability Pillars
1. Reasoning and planning engine.
2. Tool execution and control plane.
3. Memory and knowledge substrate.
4. Autonomy and scheduling engine.
5. Safety, policy, and governance layer.
6. Observability and incident replay.
7. Persona, mode, and communication protocol layer.

## Safety and Governance Invariants
1. Every action must map to a policy rule.
2. High-risk actions require explicit approval unless pre-authorized.
3. Emergency stop must halt all active agents.
4. Immutable audit logs required for all tool executions.
5. Secret material must never be exposed in plain logs.
6. Prompt injection and identity override attempts must be detected and ignored.
7. Instructions from untrusted documents/pages must never auto-execute without explicit authorization.
8. Safety refusals must include safe alternative paths when feasible.

## Success Metrics
1. Task completion rate above 90 percent for approved routine workflows.
2. False-action rate below 1 percent in production routines.
3. Mean time to detect operational anomalies below 60 seconds.
4. Mean time to recover from failed runbooks below 5 minutes.
5. User trust score (subjective weekly check) above 8/10.

## Risks
1. Over-permissioned automation causing unsafe behavior.
2. Hallucinated tool commands leading to incorrect actions.
3. Memory corruption or stale-context planning failures.
4. Security compromise through plugin/tool interface.
5. Reliability degradation under long autonomous runs.

## Mitigation Strategy
1. Capability gating and least-privilege credentials.
2. Verification loop: plan, simulate, execute, validate.
3. Signed tool contracts and strict schema validation.
4. Continuous red-team and failure-injection testing.
5. Rollback playbooks and checkpoint snapshots.

## Definition of Done (Project Level)
1. End-to-end autonomous routines execute safely across laptop and servers.
2. Voice and text interfaces are production reliable.
3. Policy and audit systems are enforced across all actions.
4. Disaster recovery and kill-switch are tested and documented.
5. Moonshot research track is active with measurable benchmark progression.
6. FRIDAY and JARVIS mode contracts pass persona and communication compliance tests.
7. Startup sequence, status check workflow, and proactive open-loop tracking are production ready.
