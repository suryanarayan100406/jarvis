"""Tests for P7-T9 forensic event export."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.audit import ImmutableAuditWriter
from runtime.security import (
    ForensicEventExportError,
    ForensicEventExporter,
    IncidentPlaybookExecutionResult,
    IncidentStepOutcome,
)
from runtime.store import LocalRunStore


class ForensicEventExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "forensics.db"
        self.store = LocalRunStore(self.db_path)
        self.store.apply_migrations()

        self.store.create_run("run-sec-1", "Respond to security alert", "boss")
        self.store.append_event(
            "run-sec-1",
            "security.alert.identity_override",
            {"actor": "boss", "token": "tok-123", "note": "override attempt"},
            severity="error",
        )
        self.store.append_event(
            "run-sec-1",
            "runtime.execute.completed",
            {"status": "ok", "duration_ms": 120},
            severity="warning",
        )
        self.store.append_event(
            "run-sec-1",
            "security.alert.policy_anomaly",
            {"password": "pw-1", "nested": {"api_key": "k-1", "safe": "y"}},
            severity="critical",
        )

        self.exporter = ForensicEventExporter(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _incident_result(self) -> IncidentPlaybookExecutionResult:
        return IncidentPlaybookExecutionResult(
            execution_id="exec-1",
            playbook_id="incident.prompt_injection",
            incident_id="inc-1",
            status="degraded",
            outcomes=(
                IncidentStepOutcome(
                    step_id="contain-1",
                    phase="containment",
                    action="isolate_session",
                    required=True,
                    status="success",
                    output={"session": "isolated"},
                    error=None,
                    started_at="2026-01-01T00:00:00Z",
                    finished_at="2026-01-01T00:00:01Z",
                ),
                IncidentStepOutcome(
                    step_id="recover-1",
                    phase="recovery",
                    action="review",
                    required=False,
                    status="failed",
                    output=None,
                    error="review delayed",
                    started_at="2026-01-01T00:00:02Z",
                    finished_at="2026-01-01T00:00:03Z",
                ),
            ),
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:00:03Z",
            metrics={"steps_total": 2},
        )

    def test_export_includes_run_and_incident_records(self) -> None:
        artifact = self.exporter.export_incident(
            incident_id="inc-1",
            run_id="run-sec-1",
            incident_result=self._incident_result(),
        )

        self.assertEqual(artifact.incident_id, "inc-1")
        self.assertEqual(artifact.run_id, "run-sec-1")
        self.assertEqual(artifact.metadata["run_event_count_filtered"], 3)
        self.assertEqual(artifact.metadata["incident_outcome_count"], 2)
        self.assertEqual(artifact.record_count, 5)
        self.assertEqual(len(artifact.digest), 64)
        self.assertTrue(any(record.source == "incident_playbook" for record in artifact.records))

    def test_export_redacts_sensitive_fields_in_payload(self) -> None:
        artifact = self.exporter.export_incident(
            incident_id="inc-1",
            run_id="run-sec-1",
            event_types={"security.alert.policy_anomaly"},
            redact_sensitive=True,
        )

        self.assertEqual(artifact.record_count, 1)
        payload = artifact.records[0].payload
        self.assertEqual(payload["password"], "[REDACTED]")
        self.assertEqual(payload["nested"]["api_key"], "[REDACTED]")
        self.assertEqual(payload["nested"]["safe"], "y")

    def test_export_filters_events_by_type_and_severity(self) -> None:
        artifact = self.exporter.export_incident(
            incident_id="inc-1",
            run_id="run-sec-1",
            event_types={"security.alert.identity_override", "security.alert.policy_anomaly"},
            severities={"critical"},
        )

        self.assertEqual(artifact.record_count, 1)
        self.assertEqual(artifact.records[0].event_type, "security.alert.policy_anomaly")
        self.assertEqual(artifact.records[0].severity, "critical")

    def test_export_includes_audit_events_and_chain_validation(self) -> None:
        audit_path = Path(self.temp_dir.name) / "audit.log"
        writer = ImmutableAuditWriter(audit_path)
        writer.append_event(
            {
                "event_type": "security.alert.identity_override",
                "severity": "critical",
                "payload": {"incident_id": "inc-1", "token": "audit-token"},
            }
        )
        writer.append_event(
            {
                "event_type": "runtime.start",
                "payload": {"ok": True},
            }
        )

        artifact = self.exporter.export_incident(
            incident_id="inc-1",
            run_id="run-sec-1",
            include_audit_events=True,
            audit_log_path=audit_path,
        )

        self.assertEqual(artifact.metadata["audit_chain_valid"], True)
        self.assertEqual(artifact.metadata["audit_event_count"], 1)
        audit_records = [record for record in artifact.records if record.source == "audit_log"]
        self.assertEqual(len(audit_records), 1)
        self.assertEqual(audit_records[0].payload["token"], "[REDACTED]")

    def test_tampered_audit_chain_is_reported(self) -> None:
        audit_path = Path(self.temp_dir.name) / "tampered-audit.log"
        writer = ImmutableAuditWriter(audit_path)
        writer.append_event(
            {
                "event_type": "security.alert.identity_override",
                "severity": "critical",
                "payload": {"incident_id": "inc-1", "note": "first"},
            }
        )
        writer.append_event(
            {
                "event_type": "security.alert.policy_anomaly",
                "severity": "error",
                "payload": {"incident_id": "inc-1", "note": "second"},
            }
        )

        lines = audit_path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[1])
        tampered["payload"]["note"] = "tampered"
        lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        artifact = self.exporter.export_incident(
            incident_id="inc-1",
            run_id="run-sec-1",
            include_audit_events=True,
            audit_log_path=audit_path,
        )

        self.assertEqual(artifact.metadata["audit_chain_valid"], False)
        issues = artifact.metadata["audit_chain_issues"]
        self.assertTrue(any("event_hash mismatch" in issue for issue in issues))

    def test_export_writes_artifact_file(self) -> None:
        output_path = Path(self.temp_dir.name) / "exports" / "incident-inc-1.json"
        artifact = self.exporter.export_incident(
            incident_id="inc-1",
            run_id="run-sec-1",
            output_path=output_path,
        )

        self.assertTrue(output_path.exists())
        written = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(written["incident_id"], artifact.incident_id)
        self.assertEqual(written["digest"], artifact.digest)

    def test_export_requires_audit_path_when_enabled(self) -> None:
        with self.assertRaises(ForensicEventExportError):
            self.exporter.export_incident(
                incident_id="inc-1",
                run_id="run-sec-1",
                include_audit_events=True,
                audit_log_path=None,
            )


if __name__ == "__main__":
    unittest.main()
