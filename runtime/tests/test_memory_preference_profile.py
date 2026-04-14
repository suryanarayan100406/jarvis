"""Tests for P5-T9 communication-style and domain-focus preference memory."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from runtime.memory import PreferenceMemoryStore, PreferenceProfileError, PreferenceProfileMemory


class PreferenceProfileMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 4, 14, 16, 0, 0, tzinfo=timezone.utc)

        def now_provider() -> datetime:
            return self.now

        self.store = PreferenceMemoryStore(now_provider=now_provider)
        self.profile_memory = PreferenceProfileMemory(self.store)

    def test_set_and_resolve_communication_preferences(self) -> None:
        written = self.profile_memory.set_communication_style(
            subject_id="boss",
            tone="direct",
            verbosity="brief",
            response_style="answer_first",
            priority=80,
        )

        resolved = self.profile_memory.resolve_profile(subject_id="boss")

        self.assertEqual(written, ("tone", "verbosity", "response_style"))
        self.assertEqual(resolved.communication.tone, "direct")
        self.assertEqual(resolved.communication.verbosity, "brief")
        self.assertEqual(resolved.communication.response_style, "answer_first")
        self.assertEqual(resolved.communication.tone_source, "boss")

    def test_set_and_resolve_domain_focus_preferences(self) -> None:
        written = self.profile_memory.set_domain_focus(
            subject_id="boss",
            topics=["Operations", "Security", "operations"],
            depth="deep",
            priority=70,
        )

        resolved = self.profile_memory.resolve_profile(subject_id="boss")

        self.assertEqual(written, ("topics", "depth"))
        self.assertEqual(resolved.domain_focus.topics, ("operations", "security"))
        self.assertEqual(resolved.domain_focus.depth, "deep")
        self.assertEqual(resolved.domain_focus.topics_source, "boss")

    def test_resolve_uses_global_fallback_when_subject_missing(self) -> None:
        self.profile_memory.set_communication_style(
            subject_id="*",
            tone="concise",
            verbosity="standard",
            response_style="stepwise",
            priority=90,
        )
        self.profile_memory.set_domain_focus(
            subject_id="*",
            topics=["reliability"],
            depth="overview",
            priority=90,
        )

        resolved = self.profile_memory.resolve_profile(subject_id="guest")

        self.assertEqual(resolved.communication.tone, "concise")
        self.assertEqual(resolved.communication.tone_source, "*")
        self.assertEqual(resolved.domain_focus.topics, ("reliability",))
        self.assertEqual(resolved.domain_focus.depth_source, "*")

    def test_subject_specific_preference_overrides_global(self) -> None:
        self.profile_memory.set_communication_style(subject_id="*", tone="balanced", priority=95)
        self.profile_memory.set_communication_style(subject_id="boss", tone="analytical", priority=40)

        resolved = self.profile_memory.resolve_profile(subject_id="boss")

        self.assertEqual(resolved.communication.tone, "analytical")
        self.assertEqual(resolved.communication.tone_source, "boss")

    def test_defaults_apply_when_no_preferences_present(self) -> None:
        resolved = self.profile_memory.resolve_profile(subject_id="unknown")

        self.assertEqual(resolved.communication.tone, "balanced")
        self.assertEqual(resolved.communication.verbosity, "standard")
        self.assertEqual(resolved.communication.response_style, "answer_first")
        self.assertEqual(resolved.domain_focus.topics, ())
        self.assertEqual(resolved.domain_focus.depth, "balanced")

    def test_invalid_values_raise(self) -> None:
        with self.assertRaises(PreferenceProfileError):
            self.profile_memory.set_communication_style(subject_id="boss", tone="aggressive")

        with self.assertRaises(PreferenceProfileError):
            self.profile_memory.set_domain_focus(subject_id="boss", depth="extreme")

        with self.assertRaises(PreferenceProfileError):
            self.profile_memory.set_domain_focus(subject_id="boss", topics=[" "])


if __name__ == "__main__":
    unittest.main()
