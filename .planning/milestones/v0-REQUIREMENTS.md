> Archived snapshot for milestone v0 on 2026-04-15.

# FRIDAY Requirements

## Functional Requirements

### FR-001 Interaction Layer
1. The system must support text command and conversational dialogue.
2. The system must support local voice input and speech output.
3. The system must support interruption, correction, and follow-up context.

### FR-002 Planning and Reasoning
1. The system must decompose user goals into executable plans.
2. The system must support multi-step task execution with checkpoints.
3. The system must provide pre-action intent summaries for medium/high-risk actions.

### FR-003 Tool and Device Control
1. The system must execute local OS tools (files, terminal, processes, services).
2. The system must orchestrate remote servers through secure connectors.
3. The system must support policy-gated browser and app automation.
4. The system must expose a standard tool contract for extensibility.

### FR-004 Knowledge and Memory
1. The system must ingest user-authorized data sources.
2. The system must provide source-grounded retrieval with citations.
3. The system must maintain short-term and long-term memory.
4. The system must support memory correction and user-editable preferences.

### FR-005 Autonomous Operations
1. The system must support event-driven triggers and scheduled routines.
2. The system must execute autonomous maintenance playbooks.
3. The system must detect failures and attempt bounded self-recovery.
4. The system must escalate to user when confidence drops below threshold.

### FR-006 Safety and Policy
1. Every tool action must be evaluated against an authorization policy.
2. The system must enforce risk tiers: low, medium, high, critical.
3. High and critical risk actions must require explicit approval unless whitelisted.
4. The system must expose an immediate kill-switch to halt all autonomous activity.

### FR-007 Security Operations
1. The system must manage secrets securely and never print raw secrets.
2. The system must generate tamper-evident audit events.
3. The system must monitor for anomalous behavior and policy violations.
4. The system must support emergency credential rotation workflows.

### FR-008 Observability
1. The system must log planning, execution, and validation events.
2. The system must provide run replay for debugging and postmortems.
3. The system must provide health telemetry for all agents and connectors.

### FR-009 Multimodal Intelligence
1. The system must interpret screenshots and UI states for automation.
2. The system must extract and summarize structured data from documents.
3. The system must align visual context with action planning.

### FR-010 Optional Physical World Integration
1. The system must support plugin-based IoT and robotics connectors.
2. The system must require explicit sandbox approval before physical actuation.
3. The system must provide physical safety interlocks for actuator commands.

### FR-011 Moonshot AGI Track
1. The system must include a benchmark harness for broad reasoning tasks.
2. The system must track transfer learning and long-horizon planning performance.
3. The system must maintain a self-improvement pipeline with rollback guardrails.
4. The system must document capability gaps against moonshot targets.

### FR-012 Identity and Addressing
1. The assistant identity must be FRIDAY with configurable persona profiles.
2. The default form of address for primary user must be Boss.
3. The system must allow form-of-address overrides by authorized preference.
4. JARVIS mode must support formal address style (Sir or Maam).

### FR-013 Communication Protocol
1. Responses must lead with answer before supporting context.
2. The system must use confidence indicators when uncertainty is material.
3. Long-running tasks must emit status updates using standardized format.
4. Urgent situations must emit priority header markers.
5. The system must avoid filler and preserve concise, direct delivery by default.

### FR-014 Operational Modes
1. The system must support War Room mode for compressed, high-urgency output.
2. The system must support Deep Research mode for cited long-form analysis.
3. The system must support Stealth mode with minimal proactive interruptions.
4. The system must support Creative mode for exploratory ideation.
5. The system must support Mission Brief mode with fixed output schema.
6. The system must support JARVIS mode for formal legacy communication profile.

### FR-015 Startup and Session Management
1. New sessions must emit a boot message including system check state.
2. Startup must report active integrations and carry-over context summary.
3. The system must maintain an open task register for session lifecycle.
4. The system must support Status check command for pending task summary.
5. Calibration of depth, tone, and domain focus should adapt naturally by interaction.

### FR-016 Proactive Assistance
1. The system must surface high-importance issues even if user has not asked.
2. The system must track open loops and initiate follow-up reminders.
3. For ambiguous requests, the system should ask one high-value clarifying question.
4. The system should suggest superior execution paths when they materially improve outcomes.

### FR-017 Confidentiality and Threat Awareness
1. Conversations must be treated as confidential by default.
2. The system must flag likely social-engineering attempts.
3. The system must detect and ignore prompt injection attempts.
4. The system must detect identity override attempts and emit an override warning.
5. Instructions embedded in documents or pages must never execute without explicit authorization.

### FR-018 Ethical Guardrails with Alternative Routing
1. The system must decline direct assistance for real-world harm.
2. The system must decline CSAM and sexual content involving minors.
3. The system must decline mass-casualty weapons and detailed attack planning.
4. When declining, the system must offer a safe alternative path where feasible.
5. For ambiguous requests, the system must prefer clarification before refusal.

## Non-Functional Requirements

### NFR-001 Locality
1. Core runtime must function without cloud APIs.
2. All core data must remain on user-controlled infrastructure.

### NFR-002 Reliability
1. Autonomous routines must meet 99.5 percent successful completion for approved low-risk tasks.
2. Recovery workflows must restore service in under 5 minutes for common failures.

### NFR-003 Performance
1. Interactive command acknowledgement should be under 1 second.
2. Standard automation plans should begin execution in under 3 seconds.

### NFR-004 Security
1. Principle of least privilege must apply to all connectors.
2. Secrets at rest must be encrypted.
3. Audit trails must be immutable and searchable.

### NFR-005 Maintainability
1. System modules must expose versioned interfaces.
2. New tools/connectors must be addable without core runtime rewrite.

### NFR-006 Persona Consistency
1. Persona and tone conformance must exceed 95 percent on conversation regression suite.
2. Addressing preference adherence must exceed 99 percent for authorized users.

### NFR-007 Session Intelligence Performance
1. Status check generation must complete in under 2 seconds on standard workload.
2. Open-loop task register accuracy must exceed 98 percent in test scenarios.

### NFR-008 Confidence and Explainability
1. Uncertain outputs must include explicit confidence scoring.
2. High-impact recommendations must include rationale and evidence references.

## Acceptance Gates
1. Gate A: Safety and policy controls pass adversarial tests.
2. Gate B: Core assistant executes representative end-to-end workflows.
3. Gate C: Autonomous routines complete with measurable reliability.
4. Gate D: Security posture validated by red-team test suite.
5. Gate E: Moonshot benchmark suite shows continuous capability improvement.
6. Gate F: Persona, addressing, and mode-switch compliance tests pass.
7. Gate G: Prompt injection and identity override resilience tests pass.
