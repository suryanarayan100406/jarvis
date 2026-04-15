> Archived snapshot for milestone v0 on 2026-04-15.

# FRIDAY Roadmap

## Milestone Overview

### Milestone 0: Foundation and Governance
Goal: Establish secure architecture, policy model, and project controls.
Deliverables:
1. System architecture specification.
2. Policy and risk-tier model.
3. Audit schema and kill-switch design.
Exit Criteria:
1. Safety invariants documented and testable.
2. Baseline repo structure and CI checks initialized.

### Milestone 1: Core Runtime and Tooling
Goal: Build local orchestrator that can plan, execute, validate, and report.
Deliverables:
1. Planner/executor runtime.
2. Tool registry and schema validation.
3. Local state store and run history.
Exit Criteria:
1. Multi-step text tasks execute deterministically with logs.

### Milestone 2: Voice, Persona, and Communication Protocols
Goal: Add local voice IO plus FRIDAY/JARVIS behavior contracts.
Deliverables:
1. Wake-word pipeline.
2. Streaming speech-to-text and text-to-speech.
3. Interrupt and confirm behavior.
4. Persona engine with FRIDAY and JARVIS profiles.
5. Status and priority message formatting contract.
6. Mode switch framework for War Room, Deep Research, Stealth, Creative, and Mission Brief.
Exit Criteria:
1. Reliable voice task execution with context retention.
2. Persona, addressing, and mode transitions pass conversation regression tests.

### Milestone 3: Control Plane for Laptop and Servers
Goal: Securely control local host and remote infrastructure.
Deliverables:
1. Host connector framework.
2. SSH/agent-based remote operations.
3. Permission-scoped command library.
Exit Criteria:
1. Approved operations run safely across all registered hosts.

### Milestone 4: Memory and Knowledge Mesh
Goal: Build persistent memory plus source-grounded retrieval.
Deliverables:
1. Knowledge ingestion pipelines.
2. Unified retrieval API with citation support.
3. Preference and routine memory models.
4. Open-loop task register and status-check summaries.
Exit Criteria:
1. Assistant answers are traceable and context-aware.
2. Session continuity and pending-task recall meet acceptance targets.

### Milestone 5: Autonomous Operations Engine
Goal: Enable event-driven and scheduled autonomous workflows.
Deliverables:
1. Trigger engine and scheduler.
2. Confidence-aware escalation logic.
3. Recovery playbooks and fallback policies.
Exit Criteria:
1. Daily autonomous routines run with bounded autonomy.

### Milestone 6: Security and Trust Hardening
Goal: Defend system against prompt injection, abuse, and compromise.
Deliverables:
1. Threat model implementation.
2. Secret lifecycle management.
3. Tamper-evident logs and incident tooling.
4. Identity override detection and protection controls.
5. Social-engineering signal detection and alerting.
Exit Criteria:
1. Red-team scenarios pass release threshold.
2. Identity and prompt injection adversarial tests pass reliability targets.

### Milestone 7: Multimodal and UI Automation
Goal: Add visual perception and UI-level reasoning for complex tasks.
Deliverables:
1. Screen understanding subsystem.
2. UI state interpreter and planner integration.
3. Safe UI automation runtime.
Exit Criteria:
1. Visual workflows execute reliably with validation.

### Milestone 8: Optional Physical Integration
Goal: Add IoT/robotics connectors behind strict safety controls.
Deliverables:
1. Physical connector SDK.
2. Command safety interlocks.
3. Simulation-first test harness.
Exit Criteria:
1. Physical actions require explicit policy and pass simulation checks.

### Milestone 9: Moonshot Capability Program
Goal: Track and improve broad intelligence capacity toward AGI-like behavior.
Deliverables:
1. Capability benchmark suite.
2. Long-horizon planning evaluation.
3. Controlled self-improvement pipeline.
Exit Criteria:
1. Quarterly benchmark improvements with no safety regression.

### Milestone 10: Production Reliability and Launch
Goal: Achieve stable daily operation with full trust controls.
Deliverables:
1. SLOs, dashboards, alerts.
2. Backup, restore, and disaster recovery.
3. Launch readiness review.
Exit Criteria:
1. 30-day stable operation with passed safety and reliability gates.

### Milestone 11: Directive Compliance and Operator Excellence
Goal: Ensure FRIDAY identity, startup behavior, and operational modes are consistently reliable.
Deliverables:
1. Boot sequence and session context recovery framework.
2. Persona compliance and tone consistency evaluator.
3. Ethical decline plus alternative-routing behavior tests.
4. Command-mode validation suite for all operational modes.
Exit Criteria:
1. Directive compliance score meets release threshold.
2. Startup and status-check workflows are reliable under load.
3. User preference adaptation is accurate and stable across sessions.

## Dependency Graph (High-Level)
1. Milestone 1 depends on Milestone 0.
2. Milestone 2 depends on Milestone 1.
3. Milestone 3 depends on Milestones 0 and 1.
4. Milestone 4 depends on Milestones 1 and 3.
5. Milestone 5 depends on Milestones 1, 3, and 4.
6. Milestone 6 depends on all previous milestones.
7. Milestone 7 depends on Milestones 4 and 5.
8. Milestone 8 depends on Milestones 5, 6, and 7.
9. Milestone 9 runs in parallel from Milestone 3 onward and informs all later milestones.
10. Milestone 10 depends on Milestones 1 through 9.
11. Milestone 11 depends on Milestones 2, 4, 6, and 10.

## Execution Rule
Each milestone must pass: plan review -> implementation -> verification -> adversarial testing -> release gate.
