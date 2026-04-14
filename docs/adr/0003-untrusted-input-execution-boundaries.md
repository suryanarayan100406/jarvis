# ADR 0003: Untrusted Input Execution Boundaries

## Status
Accepted

## Context
FRIDAY ingests content from documents, logs, web pages, and messages. Embedded instructions in untrusted inputs can attempt prompt injection or identity override.

## Decision
1. Treat all external content as untrusted by default.
2. Prohibit direct execution of instructions extracted from untrusted content.
3. Require explicit operator authorization to promote untrusted instructions to executable actions.
4. Record identity override and prompt-injection detections in audit logs.
5. Isolate untrusted content processing from privileged action pipelines.

## Consequences
1. Reduced automation convenience for third-party content.
2. Stronger resistance to prompt injection and social-engineering attacks.
3. Clearer auditability for promoted external instructions.

## Verification Strategy
1. Adversarial tests: injection attempts via documents and web content.
2. Integration tests: explicit promotion workflow before execution.
3. Policy tests: deny execution when authorization evidence is missing.
