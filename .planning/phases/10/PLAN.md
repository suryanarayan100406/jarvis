# Phase 10 Plan - Moonshot Capability Program

## Phase Goal
Establish a controlled research and evaluation program that drives measurable progress toward AGI-like breadth while preserving safety, transparency, and rollback guarantees.

## Dependencies
1. Phase 4 onward available for representative capabilities.
2. Phase 7 security controls applied to all experimental pathways.

## Scope
1. Broad capability benchmark harness.
2. Long-horizon planning and transfer evaluation.
3. Controlled self-improvement experiments.
4. Safety regression gates for capability updates.
5. Quarterly capability gap analysis reporting.

## Task Breakdown
1. P10-T1: Define benchmark taxonomy for reasoning, planning, memory, and tool use.
2. P10-T2: Implement benchmark harness runner with reproducible scoring.
3. P10-T3: Implement long-horizon mission test scenarios.
4. P10-T4: Implement cross-domain transfer task evaluation suite.
5. P10-T5: Implement self-improvement sandbox with strict isolation.
6. P10-T6: Implement experiment approval and rollback controls.
7. P10-T7: Implement safety regression gate for every model or policy change.
8. P10-T8: Implement capability trend dashboard with confidence intervals.
9. P10-T9: Implement failure taxonomy and root-cause labeling.
10. P10-T10: Implement quarterly gap-report generator against moonshot targets.
11. P10-T11: Add adversarial intelligence tests for robustness under uncertainty.
12. P10-T12: Add governance review workflow for experiment promotion.

## Deliverables
1. Capability benchmark and scoring framework.
2. Long-horizon and transfer evaluation suites.
3. Isolated self-improvement sandbox and control policies.
4. Safety regression and rollback gates for research iterations.
5. Quarterly capability and risk reporting pipeline.

## Verification Plan
1. Unit tests:
   - Benchmark scoring determinism.
   - Experiment isolation policy checks.
   - Rollback trigger correctness.
2. Integration tests:
   - Full experiment lifecycle from proposal to rollback.
   - Safety regression gate enforcement on failed scenarios.
   - Dashboard and report generation from benchmark runs.
3. Robustness tests:
   - Adversarial prompt robustness.
   - Noisy-context resilience in long-horizon tasks.
   - Transfer performance across unrelated domains.

## Exit Criteria
1. Baseline benchmark suite runs automatically and reproducibly.
2. Every capability update passes safety regression gates.
3. Quarterly reports show measurable capability movement.
4. Failed experiments can be rolled back without production impact.

## Risks and Mitigations
1. Risk: capability gains introducing safety regressions.
   Mitigation: mandatory regression gates before promotion.
2. Risk: benchmark overfitting.
   Mitigation: rotating hidden evaluation sets.
3. Risk: research scope drift without practical value.
   Mitigation: tie benchmarks to operator-facing outcomes.

## Definition of Done
Phase is done when moonshot advancement is measurable, controlled, and governed by strict safety and rollback discipline.
