"""Regression tests for P5-T12 context continuity across sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from runtime.memory import (
    IngestedDocument,
    MemoryCorrectionWorkflow,
    MemoryDomainModel,
    MemoryIndexingPipeline,
    MemoryRetrievalEngine,
    OpenLoopTaskRegister,
    PreferenceProfileMemory,
    StatusCheckCommand,
)


def _doc(
    *,
    document_id: str,
    source_type: str,
    source_id: str,
    content: str,
    content_hash: str,
    metadata: dict,
) -> IngestedDocument:
    return IngestedDocument(
        document_id=document_id,
        source_type=source_type,
        source_id=source_id,
        content=content,
        content_hash=content_hash,
        metadata=metadata,
        ingested_at="2026-04-14T19:00:00Z",
    )


class MemoryContextContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 4, 14, 19, 0, 0, tzinfo=timezone.utc)

        def now_provider() -> datetime:
            return self.now

        self.model = MemoryDomainModel(now_provider=now_provider)

    def test_long_term_and_preferences_persist_across_session_switch(self) -> None:
        self.model.short_term.put(
            session_id="session-a",
            key="active_focus",
            value="stabilize deployment",
            ttl_seconds=120,
        )
        self.model.long_term.upsert(
            namespace="ops",
            key="incident-401",
            value={"summary": "rollback executed"},
            tags=["incident"],
            source="runbook",
        )

        profile = PreferenceProfileMemory(self.model.preferences)
        profile.set_communication_style(
            subject_id="boss",
            tone="direct",
            verbosity="brief",
        )

        resumed_profile = profile.resolve_profile(subject_id="boss")

        self.assertIsNone(self.model.short_term.get("session-b", "active_focus"))
        self.assertIsNotNone(self.model.long_term.get("ops", "incident-401"))
        self.assertEqual(resumed_profile.communication.tone, "direct")

    def test_short_term_expiry_does_not_delete_long_term_context(self) -> None:
        self.model.short_term.put(
            session_id="session-a",
            key="scratchpad",
            value="temporary clue",
            ttl_seconds=30,
        )
        self.model.long_term.upsert(
            namespace="ops",
            key="incident-402",
            value={"summary": "latency investigation"},
            tags=["incident"],
            source="postmortem",
        )

        self.now = self.now + timedelta(seconds=31)
        purged = self.model.short_term.purge_expired()

        self.assertEqual(purged, 1)
        self.assertIsNone(self.model.short_term.get("session-a", "scratchpad"))
        self.assertIsNotNone(self.model.long_term.get("ops", "incident-402"))

    def test_open_loop_register_survives_status_checks_across_resumed_sessions(self) -> None:
        register = OpenLoopTaskRegister()
        register.create_task(task_id="loop-1", title="Investigate db latency", owner_id="ops", priority="high")
        register.create_task(task_id="loop-2", title="Prepare rollback plan", owner_id="ops", priority="critical")
        register.update_task("loop-1", status="in_progress")

        first_session = StatusCheckCommand(register).execute(actor_id="boss", owner_id="ops")
        resumed_session = StatusCheckCommand(register).execute(actor_id="boss", owner_id="ops")

        self.assertEqual(first_session.metrics.total, 2)
        self.assertEqual(resumed_session.metrics.total, 2)
        self.assertIn("2 open loops", resumed_session.summary)

    def test_citation_lineage_remains_stable_after_correction_and_resume(self) -> None:
        index = MemoryIndexingPipeline()
        index.index_document(
            _doc(
                document_id="mem-1",
                source_type="note",
                source_id="ops:incident-403",
                content="Incident note says API healthy",
                content_hash="h-v1",
                metadata={"topic": "incident"},
            ),
            namespace="mem",
        )

        workflow = MemoryCorrectionWorkflow(index)
        correction = workflow.apply_correction(
            namespace="mem",
            source_type="note",
            source_id="ops:incident-403",
            corrected_content="Incident note corrected: API degraded after deploy",
            actor_id="boss",
            reason="new telemetry",
        )

        resumed_engine = MemoryRetrievalEngine(index)
        result = resumed_engine.retrieve("api degraded deploy", namespace="mem", limit=1)
        citation = result.citations[0]

        self.assertEqual(correction.updated_version, 2)
        self.assertEqual(citation.index_key, "mem:note:ops:incident-403")
        self.assertEqual(citation.version, 2)
        self.assertEqual(citation.content_hash, correction.updated_content_hash)


if __name__ == "__main__":
    unittest.main()
