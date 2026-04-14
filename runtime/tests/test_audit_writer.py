"""Tests for P1-T4 immutable audit writer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.audit import ImmutableAuditWriter


class ImmutableAuditWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.temp_dir.name) / "audit.log"
        self.writer = ImmutableAuditWriter(self.audit_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_first_event_has_chain_metadata(self) -> None:
        event = self.writer.append_event({"event_type": "runtime.start", "payload": {"ok": True}})

        self.assertIn("integrity", event)
        self.assertEqual(event["integrity"]["prev_event_hash"], None)
        self.assertTrue(event["integrity"]["event_hash"])

    def test_second_event_links_previous_hash(self) -> None:
        first = self.writer.append_event({"event_type": "runtime.start", "payload": {"ok": True}})
        second = self.writer.append_event({"event_type": "runtime.step", "payload": {"step": 2}})

        self.assertEqual(second["integrity"]["prev_event_hash"], first["integrity"]["event_hash"])

    def test_verify_chain_passes_on_untampered_log(self) -> None:
        self.writer.append_event({"event_type": "runtime.start", "payload": {"ok": True}})
        self.writer.append_event({"event_type": "runtime.step", "payload": {"step": 2}})

        valid, issues = self.writer.verify_chain()

        self.assertTrue(valid)
        self.assertEqual(issues, [])

    def test_verify_chain_detects_tampering(self) -> None:
        self.writer.append_event({"event_type": "runtime.start", "payload": {"ok": True}})
        self.writer.append_event({"event_type": "runtime.step", "payload": {"step": 2}})

        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[1])
        tampered["payload"]["step"] = 999
        lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        self.audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        valid, issues = self.writer.verify_chain()

        self.assertFalse(valid)
        self.assertTrue(any("event_hash mismatch" in issue for issue in issues))

    def test_chain_state_is_preserved_between_instances(self) -> None:
        first = self.writer.append_event({"event_type": "runtime.start", "payload": {"ok": True}})

        writer2 = ImmutableAuditWriter(self.audit_path)
        second = writer2.append_event({"event_type": "runtime.step", "payload": {"step": 2}})

        self.assertEqual(second["integrity"]["chain_id"], first["integrity"]["chain_id"])
        self.assertEqual(second["integrity"]["prev_event_hash"], first["integrity"]["event_hash"])


if __name__ == "__main__":
    unittest.main()
