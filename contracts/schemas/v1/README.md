# FRIDAY Contract Schemas v1

This package defines the canonical JSON Schema contracts for Phase 1 Task 1.

## Files
1. common.schema.json - shared definitions.
2. tool-request.schema.json - request envelope for tool execution.
3. tool-response.schema.json - response envelope for tool outcomes.
4. tool-error.schema.json - canonical error object.
5. telemetry-envelope.schema.json - telemetry and audit event envelope.
6. identity-directive.schema.json - assistant identity and addressing preference contract.
7. session-protocol.schema.json - startup boot and status/priority message protocol contract.

## Versioning
1. Current schema_version is 1.0.0.
2. Breaking changes require a new major folder (for example v2).
3. Non-breaking additive fields should remain optional.

## Contract Rules
1. Every request, response, error, and telemetry event must include schema_version.
2. All IDs are UUID format.
3. All timestamps use ISO-8601 date-time format.
4. All envelopes include trace context.
5. High-risk and critical operations must include policy context/decision data.

## Next Step
P1-T2 should wire runtime middleware that validates incoming and outgoing payloads against these schemas.
