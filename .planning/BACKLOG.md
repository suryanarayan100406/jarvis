# FRIDAY Build Backlog (First 30 Tasks)

## Ordering Rule
Complete tasks in order unless explicitly marked parallel-safe.

## Execution Gate Rule
1. After each task completion, run task-specific verification tests before starting the next task.
2. Advance to the next task only if verification passes against the task Done criteria.
3. If verification fails, fix immediately and re-run verification until pass.
4. After each verified task, commit and push to the configured remote repository.

## Tasks
1. T001 - Create monorepo structure for core, connectors, memory, policy, and ui modules. Done when all module skeletons build.
2. T002 - Define canonical tool contract (request, response, error, telemetry schema). Done when schema validation tests pass.
3. T003 - Implement policy engine v1 with risk tiers and allow/deny decisions. Done when policy unit tests cover all risk classes.
4. T004 - Implement immutable audit event writer with hash chaining. Done when tamper test detects modified events.
5. T005 - Add emergency kill-switch controller and global stop signal. Done when active runs terminate in under 1 second.
6. T006 - Implement run state machine (plan -> execute -> validate -> report). Done when integration test completes all stages.
7. T007 - Build planner interface with deterministic plan serialization. Done when same input yields stable plan outputs.
8. T008 - Build executor interface with tool-call sandboxing and timeout guards. Done when runaway command tests fail safely.
9. T009 - Implement local run store (sqlite or equivalent local db) with migration framework. Done when migration rollback test passes.
10. T010 - Build tool registry with signed tool manifest and capability metadata. Done when unsigned tools are rejected.
11. T011 - Implement terminal tool adapter for local command execution with policy checks. Done when blocked command tests pass.
12. T012 - Implement filesystem tool adapter with path sandbox constraints. Done when escape-path tests are blocked.
13. T013 - Add process/service control adapter with explicit operation scopes. Done when unauthorized service control is denied.
14. T014 - Implement connector manager for remote hosts and identity mapping. Done when host policy scoping works in tests.
15. T015 - Add secure SSH connector with per-host command allowlists. Done when cross-host policy leakage tests fail safely.
16. T016 - Implement secrets manager abstraction with encrypted local storage. Done when secret-at-rest inspection is encrypted.
17. T017 - Implement voice pipeline scaffold (wake trigger, stt stream, tts output). Done when local voice round-trip works.
18. T018 - Add conversational context manager with interruption handling. Done when interrupted commands reconcile correctly.
19. T019 - Build memory service (working memory, episodic logs, preference store). Done when memory retrieval tests pass.
20. T020 - Build ingestion pipeline for notes, docs, code, and logs. Done when indexed source retrieval is available.
21. T021 - Implement retrieval layer with citation and confidence scoring. Done when every answer can link to source ids.
22. T022 - Implement scheduler for cron/time/event triggers. Done when scheduled tasks execute within timing tolerance.
23. T023 - Build autonomy guardrails (confidence thresholds and escalation rules). Done when low-confidence actions request approval.
24. T024 - Implement runbook engine for routine maintenance workflows. Done when routine workflows complete with validation.
25. T025 - Add anomaly detection hooks for behavior and policy drift. Done when synthetic anomaly tests trigger alerts.
26. T026 - Implement dashboard API for live runs, policy decisions, and health metrics. Done when core metrics stream in real-time.
27. T027 - Add multimodal perception module for screenshot understanding. Done when UI-state extraction tests pass.
28. T028 - Integrate UI automation toolchain with validation checkpoints. Done when critical UI flows execute safely.
29. T029 - Implement benchmark harness for moonshot capability tracking. Done when baseline benchmark report is generated.
30. T030 - Run full adversarial test suite and production readiness review. Done when all release gates pass.
31. T031 - Implement assistant identity profile engine for FRIDAY and JARVIS modes. Done when mode profile contract tests pass.
32. T032 - Implement addressing preference service with Boss default and authorized overrides. Done when addressing tests pass by user role.
33. T033 - Implement response protocol formatter (answer-first, confidence tags, priority markers). Done when formatting suite passes.
34. T034 - Implement long-running status emitter using [STATUS: In Progress | x%] contract. Done when status updates are emitted on schedule.
35. T035 - Implement urgency formatter using [PRIORITY: CRITICAL] contract. Done when urgent events are normalized.
36. T036 - Implement operational mode switch manager with policy-aware transitions. Done when all mode transitions validate.
37. T037 - Implement War Room mode rendering and triage prioritization. Done when high-urgency outputs compress correctly.
38. T038 - Implement Deep Research mode pipeline with citation and confidence scoring. Done when research reports pass citation checks.
39. T039 - Implement Stealth mode gating for minimal unsolicited output. Done when proactive output is suppressed except critical alerts.
40. T040 - Implement Creative mode and Mission Brief output schema. Done when schema validation passes for mission briefs.
41. T041 - Implement startup boot sequence and session carry-over summary contract. Done when startup integration tests pass.
42. T042 - Implement open-loop task register and Status check command. Done when open-loop tracking accuracy meets target.
43. T043 - Implement prompt-injection and identity-override detection pipeline. Done when adversarial identity tests pass.
44. T044 - Implement ethical refusal router with safe alternative-path generation. Done when refusal behavior suite passes.
45. T045 - Build conversation regression harness for persona and tone consistency. Done when compliance score meets threshold.

## Parallel-Safe Windows
1. T017 and T019 can run in parallel after T010.
2. T020 and T022 can run in parallel after T019.
3. T027 and T029 can run in parallel after T024.
4. T033 and T042 can run in parallel after T019.
5. T036 and T043 can run in parallel after T030.
6. T038 and T045 can run in parallel after T041.
