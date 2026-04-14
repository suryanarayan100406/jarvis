"""Tests for P5-T1 memory domain model stores."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from runtime.memory import MemoryDomainModel, MemoryDomainError


class MemoryDomainModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)

        def now_provider() -> datetime:
            return self.now

        self.model = MemoryDomainModel(now_provider=now_provider)

    def test_short_term_put_get_and_versioning(self) -> None:
        first = self.model.short_term.put(
            session_id="session-1",
            key="current_goal",
            value="Collect deployment diagnostics",
            ttl_seconds=120,
            tags=["task"],
        )
        second = self.model.short_term.put(
            session_id="session-1",
            key="current_goal",
            value="Collect deployment diagnostics and logs",
            ttl_seconds=120,
            tags=["task", "logs"],
        )
        loaded = self.model.short_term.get("session-1", "current_goal")

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertEqual(first.memory_id, second.memory_id)
        self.assertEqual(loaded.value, "Collect deployment diagnostics and logs")
        self.assertEqual(loaded.tags, ("logs", "task"))

    def test_short_term_expiration_and_purge(self) -> None:
        self.model.short_term.put(
            session_id="session-2",
            key="ephemeral_note",
            value="Temporary context",
            ttl_seconds=30,
        )
        self.now = self.now + timedelta(seconds=31)

        loaded = self.model.short_term.get("session-2", "ephemeral_note")
        purged = self.model.short_term.purge_expired()

        self.assertIsNone(loaded)
        self.assertEqual(purged, 1)

    def test_long_term_upsert_versioning_and_tag_filter(self) -> None:
        first = self.model.long_term.upsert(
            namespace="project",
            key="roadmap",
            value={"phase": 5},
            tags=["planning", "roadmap"],
            source="ROADMAP.md",
        )
        second = self.model.long_term.upsert(
            namespace="project",
            key="roadmap",
            value={"phase": 6},
            tags=["planning"],
            source="ROADMAP.md",
        )

        listed = self.model.long_term.list(namespace="project", tag="planning")

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].value["phase"], 6)

    def test_preference_resolution_prefers_subject_specific_over_global(self) -> None:
        self.model.preferences.set_preference(
            subject_id="*",
            category="communication",
            key="tone",
            value="concise",
            priority=90,
        )
        self.model.preferences.set_preference(
            subject_id="boss",
            category="communication",
            key="tone",
            value="direct",
            priority=50,
        )

        boss_preference = self.model.preferences.resolve_preference(
            subject_id="boss",
            category="communication",
            key="tone",
        )
        guest_preference = self.model.preferences.resolve_preference(
            subject_id="guest",
            category="communication",
            key="tone",
        )

        self.assertEqual(boss_preference.value, "direct")
        self.assertEqual(guest_preference.value, "concise")

    def test_preference_update_increments_version(self) -> None:
        first = self.model.preferences.set_preference(
            subject_id="boss",
            category="communication",
            key="verbosity",
            value="brief",
        )
        second = self.model.preferences.set_preference(
            subject_id="boss",
            category="communication",
            key="verbosity",
            value="standard",
        )

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertEqual(first.preference_id, second.preference_id)

    def test_invalid_ttl_and_priority_raise(self) -> None:
        with self.assertRaises(MemoryDomainError):
            self.model.short_term.put(
                session_id="session-3",
                key="invalid",
                value="x",
                ttl_seconds=0,
            )

        with self.assertRaises(MemoryDomainError):
            self.model.preferences.set_preference(
                subject_id="boss",
                category="communication",
                key="tone",
                value="direct",
                priority=101,
            )


if __name__ == "__main__":
    unittest.main()
