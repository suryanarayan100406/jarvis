# Phase 3 Plan - Voice, Persona, and Communication Protocols

## Phase Goal
Deliver local voice interaction and the FRIDAY and JARVIS communication contract, including addressing preferences, mode switching, and status protocol behavior.

## Dependencies
1. Phase 2 completed and verified.
2. Identity directives from Phase 1 available as runtime contract.

## Scope
1. Wake pipeline and streaming speech interface.
2. Persona profile engine for FRIDAY and JARVIS.
3. Addressing preference service with Boss default.
4. Communication formatter enforcing answer-first output.
5. Operational mode switch support for all defined modes.
6. Status and urgency message protocols.

## Task Breakdown
1. P3-T1: Implement wake trigger with local phrase detection.
2. P3-T2: Implement streaming speech-to-text and text-to-speech adapters.
3. P3-T3: Implement conversational turn manager with interruption handling.
4. P3-T4: Implement persona profile engine for FRIDAY and JARVIS.
5. P3-T5: Implement addressing preference layer with role-based overrides.
6. P3-T6: Implement response formatter for answer-first and confidence tags.
7. P3-T7: Implement status update formatter using required status contract.
8. P3-T8: Implement priority formatter for urgent and critical events.
9. P3-T9: Implement mode switch manager for War Room, Deep Research, Stealth, Creative, and Mission Brief.
10. P3-T10: Implement Mission Brief schema renderer.
11. P3-T11: Add conversation regression tests for persona and tone consistency.
12. P3-T12: Add voice latency and reliability tests under noisy input conditions.

## Deliverables
1. Local voice interface subsystem.
2. Persona and addressing engine.
3. Communication protocol formatter package.
4. Mode switch and output schema manager.
5. Conversation and voice quality regression suite.

## Verification Plan
1. Unit tests:
   - Persona profile selection and fallback.
   - Addressing preference resolution by operator role.
   - Response formatting and confidence tag logic.
2. Integration tests:
   - Voice command to plan execution pipeline.
   - Mid-response interruption and correction recovery.
   - Mode switch persistence within session.
3. Compliance tests:
   - Boss addressing by default in FRIDAY mode.
   - Sir or Maam addressing in JARVIS mode.
   - Required status and priority format usage.
4. Performance checks:
   - Voice round-trip latency targets.
   - Streaming stability over long sessions.

## Exit Criteria
1. Voice workflow is reliable with interruption recovery.
2. Persona and addressing behavior match contract requirements.
3. All operational modes switch and render correctly.
4. Communication compliance suite meets acceptance threshold.

## Risks and Mitigations
1. Risk: persona drift over long sessions.
   Mitigation: periodic profile re-anchor checkpoints.
2. Risk: speech errors causing bad command interpretation.
   Mitigation: confidence gating and confirmation strategy by risk tier.
3. Risk: format regressions after feature changes.
   Mitigation: strict contract tests in CI.

## Definition of Done
Phase is done when voice interaction and Stark-style communication behavior are stable, testable, and contract-compliant.
