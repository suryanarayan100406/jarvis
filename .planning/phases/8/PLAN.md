# Phase 8 Plan - Multimodal and UI Automation

## Phase Goal
Add visual understanding and safe UI-level automation so FRIDAY can reason over screens, documents, and interfaces while validating actions before execution.

## Dependencies
1. Phase 5 and Phase 6 completed and verified.
2. Security controls from Phase 7 available for UI action safeguards.

## Scope
1. Screenshot and UI-state perception.
2. OCR and structured extraction pipelines.
3. Visual-context-aware action planning.
4. UI automation executor with pre and post checks.
5. Cross-modal evidence linking to retrieval system.

## Task Breakdown
1. P8-T1: Implement screenshot ingestion and normalization pipeline.
2. P8-T2: Implement OCR and layout analysis for text-rich interfaces.
3. P8-T3: Implement UI element grounding model and state representation.
4. P8-T4: Implement visual planner integration with runtime action stages.
5. P8-T5: Implement safe UI action executor with confirmation checkpoints.
6. P8-T6: Implement before and after state validation for critical UI tasks.
7. P8-T7: Implement document and image summary extraction with citations.
8. P8-T8: Implement multimodal evidence store tied to memory system.
9. P8-T9: Implement fallback strategy when visual confidence is low.
10. P8-T10: Add regression tests for common desktop and browser workflows.
11. P8-T11: Add adversarial tests for deceptive UI patterns.
12. P8-T12: Add performance tests for visual processing latency.

## Deliverables
1. Multimodal perception subsystem.
2. UI state interpreter and action planner integration.
3. Safe UI automation runtime with validation gates.
4. Cross-modal evidence and citation pipeline.
5. Regression and adversarial test suite for visual workflows.

## Verification Plan
1. Unit tests:
   - OCR extraction correctness.
   - UI element grounding confidence thresholds.
   - Validation gate decision logic.
2. Integration tests:
   - End-to-end UI workflows from perception to execution.
   - Low-confidence fallback behavior.
   - Multimodal retrieval with visual citations.
3. Adversarial tests:
   - Fake button and deceptive overlay scenarios.
   - Mismatched state replay attacks.
   - UI spoofing attempts against action planner.
4. Performance checks:
   - Screenshot-to-plan latency budget.
   - Throughput under batched visual tasks.

## Exit Criteria
1. Visual workflows execute reliably with validation checkpoints.
2. OCR and UI grounding meet quality thresholds.
3. Low-confidence cases escalate instead of acting blindly.
4. Deceptive UI patterns are detected and blocked in tests.

## Risks and Mitigations
1. Risk: UI variability causing brittle automation.
   Mitigation: state abstraction and resilient selector strategies.
2. Risk: false confidence on ambiguous screens.
   Mitigation: confidence gating and mandatory confirmation for high-risk actions.
3. Risk: high compute cost for multimodal processing.
   Mitigation: adaptive sampling and caching of stable UI states.

## Definition of Done
Phase is done when FRIDAY can perceive and act on visual interfaces safely, with verifiable state checks and confidence-aware behavior.
