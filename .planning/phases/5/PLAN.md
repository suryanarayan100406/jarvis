# Phase 5 Plan - Memory and Knowledge Mesh

## Phase Goal
Build persistent memory and source-grounded retrieval so FRIDAY can retain context, track open loops, and deliver evidence-backed responses.

## Dependencies
1. Phase 2 and Phase 4 completed and verified.
2. Control-plane and runtime event streams available for ingestion.

## Scope
1. Working memory, episodic memory, and preference memory layers.
2. Ingestion pipelines for notes, code, docs, logs, and transcripts.
3. Retrieval service with citation and confidence scoring.
4. Open-loop task register and Status check response generation.
5. Memory correction and preference update workflows.

## Task Breakdown
1. P5-T1: Define memory domain model for short-term, long-term, and preference stores.
2. P5-T2: Implement ingestion adapters for files, notes, logs, and command history.
3. P5-T3: Implement indexing pipeline with deduplication and version tracking.
4. P5-T4: Implement retrieval engine with source citation binding.
5. P5-T5: Implement confidence scoring and evidence ranking.
6. P5-T6: Implement open-loop task register service.
7. P5-T7: Implement Status check command and summary renderer.
8. P5-T8: Implement user-correctable memory update workflow.
9. P5-T9: Implement preference memory for communication style and domain focus.
10. P5-T10: Add memory privacy filters and redaction-aware retrieval.
11. P5-T11: Add retrieval quality tests for relevance and citation fidelity.
12. P5-T12: Add regression tests for context continuity across sessions.

## Deliverables
1. Multi-layer memory service with persistent storage.
2. Ingestion and indexing framework with provenance metadata.
3. Retrieval and confidence API with source references.
4. Task register and Status check workflow.
5. Memory correction and preference management interfaces.

## Verification Plan
1. Unit tests:
   - Memory write and recall correctness.
   - Citation attachment and source traceability.
   - Preference resolution precedence rules.
2. Integration tests:
   - Session continuity from shutdown to resume.
   - Task register updates during autonomous runs.
   - Context-aware responses after multi-source ingestion.
3. Quality tests:
   - Retrieval relevance on benchmark query set.
   - Hallucination reduction with evidence gating.
4. Security tests:
   - Sensitive data redaction in retrieval output.
   - Unauthorized memory partition access attempts.

## Exit Criteria
1. Context continuity works reliably across sessions.
2. Responses include accurate citations and confidence markers.
3. Status check reports open tasks and pending loops correctly.
4. Memory edits and preference updates are user-controllable and auditable.

## Risks and Mitigations
1. Risk: stale or conflicting memory entries.
   Mitigation: versioned records and conflict-resolution strategy.
2. Risk: retrieval latency under large corpora.
   Mitigation: indexed caching and query budget controls.
3. Risk: privacy leaks through retrieval.
   Mitigation: memory partitioning and strict access filters.

## Definition of Done
Phase is done when FRIDAY can reliably remember, retrieve, and explain context with traceable evidence and stable session continuity.
