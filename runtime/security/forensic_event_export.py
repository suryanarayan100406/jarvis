"""Forensic event export for post-incident analysis."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from runtime.store import LocalRunStore, RunEvent

from .incident_playbooks import IncidentPlaybookExecutionResult, IncidentStepOutcome


@dataclass(frozen=True)
class ForensicEventRecord:
    source: str
    record_id: str
    event_type: str
    severity: str
    timestamp: str
    payload: dict[str, Any]
    evidence_hash: str


@dataclass(frozen=True)
class ForensicExportArtifact:
    incident_id: str
    run_id: str
    exported_at: str
    record_count: int
    records: tuple[ForensicEventRecord, ...]
    metadata: dict[str, Any]
    digest: str


class ForensicEventExportError(ValueError):
    """Raised when forensic export requests are invalid."""


class ForensicEventExporter:
    """Builds deterministic forensic bundles from run, incident, and audit evidence."""

    _default_sensitive_markers = (
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "credential",
        "authorization",
        "access_key",
    )

    def __init__(
        self,
        store: LocalRunStore,
        *,
        default_limit: int | None = None,
        sensitive_markers: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        if default_limit is not None and default_limit < 1:
            raise ForensicEventExportError("default_limit must be at least 1")

        markers = sensitive_markers or self._default_sensitive_markers
        normalized_markers = tuple(sorted({_normalize_required(marker, "sensitive_marker").lower() for marker in markers}))
        if not normalized_markers:
            raise ForensicEventExportError("sensitive_markers cannot be empty")

        self.store = store
        self.default_limit = default_limit
        self.sensitive_markers = normalized_markers

    def export_incident(
        self,
        *,
        incident_id: str,
        run_id: str,
        event_types: Iterable[str] | None = None,
        severities: Iterable[str] | None = None,
        include_payload: bool = True,
        redact_sensitive: bool = True,
        include_audit_events: bool = False,
        audit_log_path: str | Path | None = None,
        incident_result: IncidentPlaybookExecutionResult | None = None,
        output_path: str | Path | None = None,
        limit: int | None = None,
    ) -> ForensicExportArtifact:
        """Export filtered forensic records for incident post-mortem workflows."""
        normalized_incident = _normalize_required(incident_id, "incident_id")
        normalized_run_id = _normalize_required(run_id, "run_id")
        event_type_filter = _normalize_filter(event_types)
        severity_filter = _normalize_filter(severities)

        effective_limit = self.default_limit if limit is None else limit
        if effective_limit is not None and effective_limit < 1:
            raise ForensicEventExportError("limit must be at least 1")

        if incident_result is not None and incident_result.incident_id != normalized_incident:
            raise ForensicEventExportError("incident_result.incident_id does not match incident_id")

        try:
            run = self.store.get_run(normalized_run_id)
        except KeyError as exc:
            raise ForensicEventExportError(f"Run not found: {normalized_run_id}") from exc

        all_events = self.store.list_events(normalized_run_id, limit=None)
        filtered_events = self._filter_run_events(
            all_events,
            event_types=event_type_filter,
            severities=severity_filter,
        )
        if effective_limit is not None:
            filtered_events = filtered_events[:effective_limit]

        records: list[ForensicEventRecord] = []
        run_records = self._build_run_records(
            filtered_events,
            include_payload=include_payload,
            redact_sensitive=redact_sensitive,
        )
        records.extend(run_records)

        incident_records = self._build_incident_records(
            incident_result,
            include_payload=include_payload,
            redact_sensitive=redact_sensitive,
        )
        records.extend(incident_records)

        audit_records: list[ForensicEventRecord] = []
        audit_chain_valid: bool | None = None
        audit_chain_issues: tuple[str, ...] = ()
        if include_audit_events:
            if audit_log_path is None:
                raise ForensicEventExportError("audit_log_path is required when include_audit_events is True")

            audit_path = Path(audit_log_path)
            if not audit_path.exists():
                raise ForensicEventExportError(f"Audit log not found: {audit_path}")

            audit_records, parse_issues = self._load_audit_records(
                audit_path,
                include_payload=include_payload,
                redact_sensitive=redact_sensitive,
                event_types=event_type_filter,
                severities=severity_filter,
            )
            chain_valid, chain_issues = _verify_audit_chain(audit_path)
            audit_chain_valid = chain_valid
            audit_chain_issues = tuple(list(chain_issues) + list(parse_issues))
            records.extend(audit_records)

        records.sort(key=lambda item: (_safe_parse_iso(item.timestamp), item.source, item.record_id))

        metadata: dict[str, Any] = {
            "export_mode": "forensic_incident_bundle",
            "run_status": run.status,
            "run_event_count_total": len(all_events),
            "run_event_count_filtered": len(filtered_events),
            "incident_outcome_count": len(incident_records),
            "audit_event_count": len(audit_records),
            "total_record_count": len(records),
            "include_payload": include_payload,
            "redact_sensitive": redact_sensitive,
            "event_type_filter": sorted(event_type_filter) if event_type_filter is not None else None,
            "severity_filter": sorted(severity_filter) if severity_filter is not None else None,
            "limit": effective_limit,
            "audit_chain_valid": audit_chain_valid,
            "audit_chain_issues": list(audit_chain_issues),
        }

        exported_at = _utc_now_iso()
        digest = _hash_payload(
            {
                "incident_id": normalized_incident,
                "run_id": normalized_run_id,
                "records": [asdict(record) for record in records],
                "metadata": metadata,
            }
        )
        artifact = ForensicExportArtifact(
            incident_id=normalized_incident,
            run_id=normalized_run_id,
            exported_at=exported_at,
            record_count=len(records),
            records=tuple(records),
            metadata=metadata,
            digest=digest,
        )

        if output_path is not None:
            export_path = Path(output_path)
            export_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "incident_id": artifact.incident_id,
                "run_id": artifact.run_id,
                "exported_at": artifact.exported_at,
                "record_count": artifact.record_count,
                "records": [asdict(record) for record in artifact.records],
                "metadata": artifact.metadata,
                "digest": artifact.digest,
            }
            export_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        return artifact

    def _filter_run_events(
        self,
        events: list[RunEvent],
        *,
        event_types: set[str] | None,
        severities: set[str] | None,
    ) -> list[RunEvent]:
        filtered: list[RunEvent] = []
        for event in events:
            event_type = event.event_type.strip().lower()
            severity = event.severity.strip().lower()

            if event_types is not None and event_type not in event_types:
                continue
            if severities is not None and severity not in severities:
                continue
            filtered.append(event)
        return filtered

    def _build_run_records(
        self,
        events: list[RunEvent],
        *,
        include_payload: bool,
        redact_sensitive: bool,
    ) -> list[ForensicEventRecord]:
        records: list[ForensicEventRecord] = []
        for event in events:
            payload: dict[str, Any]
            if include_payload:
                payload = deepcopy(event.payload)
                if redact_sensitive:
                    payload = self._redact_payload(payload)
            else:
                payload = {"redacted": True}

            evidence_hash = _hash_payload(
                {
                    "event_id": event.event_id,
                    "run_id": event.run_id,
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "created_at": event.created_at,
                    "payload": payload,
                }
            )
            records.append(
                ForensicEventRecord(
                    source="run_store",
                    record_id=f"run_event:{event.event_id}",
                    event_type=event.event_type,
                    severity=event.severity,
                    timestamp=event.created_at,
                    payload=payload,
                    evidence_hash=evidence_hash,
                )
            )
        return records

    def _build_incident_records(
        self,
        incident_result: IncidentPlaybookExecutionResult | None,
        *,
        include_payload: bool,
        redact_sensitive: bool,
    ) -> list[ForensicEventRecord]:
        if incident_result is None:
            return []

        records: list[ForensicEventRecord] = []
        for index, outcome in enumerate(incident_result.outcomes, start=1):
            payload = self._incident_payload(outcome, include_payload=include_payload, redact_sensitive=redact_sensitive)
            severity = _severity_for_outcome(outcome)
            event_type = f"incident.{outcome.phase}.{outcome.status}"
            evidence_hash = _hash_payload(
                {
                    "incident_id": incident_result.incident_id,
                    "execution_id": incident_result.execution_id,
                    "step_id": outcome.step_id,
                    "phase": outcome.phase,
                    "status": outcome.status,
                    "required": outcome.required,
                    "payload": payload,
                }
            )
            records.append(
                ForensicEventRecord(
                    source="incident_playbook",
                    record_id=f"incident_step:{index}:{outcome.step_id}",
                    event_type=event_type,
                    severity=severity,
                    timestamp=outcome.finished_at,
                    payload=payload,
                    evidence_hash=evidence_hash,
                )
            )
        return records

    def _incident_payload(
        self,
        outcome: IncidentStepOutcome,
        *,
        include_payload: bool,
        redact_sensitive: bool,
    ) -> dict[str, Any]:
        base = {
            "step_id": outcome.step_id,
            "phase": outcome.phase,
            "action": outcome.action,
            "required": outcome.required,
            "status": outcome.status,
            "error": outcome.error,
        }
        if include_payload:
            base["output"] = deepcopy(outcome.output)
            if redact_sensitive:
                return self._redact_payload(base)
            return base
        base["output"] = {"redacted": True}
        return base

    def _load_audit_records(
        self,
        audit_log_path: Path,
        *,
        include_payload: bool,
        redact_sensitive: bool,
        event_types: set[str] | None,
        severities: set[str] | None,
    ) -> tuple[list[ForensicEventRecord], tuple[str, ...]]:
        records: list[ForensicEventRecord] = []
        parse_issues: list[str] = []

        for line_number, raw_line in enumerate(audit_log_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw_line.strip():
                continue

            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                parse_issues.append(f"line {line_number}: invalid json")
                continue

            event_type = str(event.get("event_type", "unknown")).strip().lower()
            severity = str(event.get("severity", "info")).strip().lower()

            if event_types is not None and event_type not in event_types:
                continue
            if severities is not None and severity not in severities:
                continue
            if not event_type.startswith("security."):
                continue

            raw_payload = event.get("payload", {})
            if include_payload:
                payload = deepcopy(raw_payload) if isinstance(raw_payload, dict) else {"value": raw_payload}
                if redact_sensitive:
                    payload = self._redact_payload(payload)
            else:
                payload = {"redacted": True}

            integrity = event.get("integrity", {}) if isinstance(event.get("integrity", {}), dict) else {}
            event_hash = integrity.get("event_hash")
            evidence_hash = str(event_hash) if event_hash else _hash_payload(event)
            timestamp = str(event.get("timestamp", _utc_now_iso()))
            record_id = str(event.get("event_id", f"audit_line:{line_number}"))

            records.append(
                ForensicEventRecord(
                    source="audit_log",
                    record_id=record_id,
                    event_type=event_type,
                    severity=severity,
                    timestamp=timestamp,
                    payload=payload,
                    evidence_hash=evidence_hash,
                )
            )

        return records, tuple(parse_issues)

    def _redact_payload(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            redacted: dict[str, Any] = {}
            for key, value in payload.items():
                normalized_key = " ".join(str(key).split())
                key_lower = normalized_key.lower()
                if any(marker in key_lower for marker in self.sensitive_markers):
                    redacted[normalized_key] = "[REDACTED]"
                else:
                    redacted[normalized_key] = self._redact_payload(value)
            return redacted

        if isinstance(payload, list):
            return [self._redact_payload(item) for item in payload]

        if isinstance(payload, tuple):
            return [self._redact_payload(item) for item in payload]

        return payload


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ForensicEventExportError(f"{field_name} is required")
    return normalized


def _normalize_filter(values: Iterable[str] | None) -> set[str] | None:
    if values is None:
        return None
    normalized = {_normalize_required(value, "filter_value").lower() for value in values}
    return normalized or None


def _severity_for_outcome(outcome: IncidentStepOutcome) -> str:
    if outcome.status == "failed" and outcome.required:
        return "critical"
    if outcome.status == "failed":
        return "warning"
    return "info"


def _safe_parse_iso(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hash_payload(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _verify_audit_chain(audit_log_path: Path) -> tuple[bool, tuple[str, ...]]:
    issues: list[str] = []
    expected_prev: str | None = None
    expected_chain_id: str | None = None

    lines = audit_log_path.read_text(encoding="utf-8").splitlines()
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue

        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            issues.append(f"line {line_number}: invalid json")
            continue

        integrity = event.get("integrity", {}) if isinstance(event.get("integrity", {}), dict) else {}
        chain_id = integrity.get("chain_id")
        prev_hash = integrity.get("prev_event_hash")
        event_hash = integrity.get("event_hash")

        if expected_chain_id is None:
            expected_chain_id = chain_id
        elif chain_id != expected_chain_id:
            issues.append(f"line {line_number}: chain_id mismatch")

        if prev_hash != expected_prev:
            issues.append(f"line {line_number}: prev_event_hash mismatch")

        computed_hash = _compute_audit_event_hash(event)
        if event_hash != computed_hash:
            issues.append(f"line {line_number}: event_hash mismatch")

        expected_prev = event_hash

    return len(issues) == 0, tuple(issues)


def _compute_audit_event_hash(event: dict[str, Any]) -> str:
    hash_source = deepcopy(event)
    integrity = dict(hash_source.get("integrity", {}))
    integrity["event_hash"] = ""
    hash_source["integrity"] = integrity
    canonical = json.dumps(hash_source, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "ForensicEventExportError",
    "ForensicEventExporter",
    "ForensicEventRecord",
    "ForensicExportArtifact",
]
