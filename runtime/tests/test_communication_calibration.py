"""Tests for P12-T3 communication calibration tracker."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from runtime.memory import PreferenceMemoryStore, PreferenceProfileMemory
from runtime.persona import (
    CommunicationCalibrationError,
    CommunicationCalibrationSignal,
    CommunicationCalibrationTracker,
)


class CommunicationCalibrationTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)

        def now_provider() -> datetime:
            return now

        store = PreferenceMemoryStore(now_provider=now_provider)
        self.profile_memory = PreferenceProfileMemory(store)
        self.tracker = CommunicationCalibrationTracker(self.profile_memory)

    def test_build_snapshot_recommends_values_from_signals(self) -> None:
        self._record("sig-1", tone="direct", depth="deep", verbosity="brief")
        self._record("sig-2", tone="direct", depth="deep", verbosity="brief")
        self._record("sig-3", tone="analytical", depth="deep", verbosity="brief")

        snapshot = self.tracker.build_snapshot(subject_id="boss")

        self.assertEqual(snapshot.recommended_tone, "direct")
        self.assertEqual(snapshot.recommended_depth, "deep")
        self.assertEqual(snapshot.recommended_verbosity, "brief")
        self.assertGreaterEqual(snapshot.confidence, 0.6)
        self.assertEqual(snapshot.sample_size, 3)

    def test_snapshot_uses_profile_defaults_when_no_signals(self) -> None:
        snapshot = self.tracker.build_snapshot(subject_id="boss")

        self.assertEqual(snapshot.recommended_tone, "balanced")
        self.assertEqual(snapshot.recommended_depth, "balanced")
        self.assertEqual(snapshot.recommended_verbosity, "standard")
        self.assertEqual(snapshot.confidence, 0.0)

    def test_apply_calibration_updates_preferences_when_confident(self) -> None:
        self._record("sig-1", tone="direct", depth="deep", verbosity="brief")
        self._record("sig-2", tone="direct", depth="deep", verbosity="brief")
        self._record("sig-3", tone="direct", depth="deep", verbosity="brief")

        result = self.tracker.apply_calibration(subject_id="boss", min_confidence=0.4)

        self.assertTrue(result.applied)
        resolved = self.profile_memory.resolve_profile(subject_id="boss")
        self.assertEqual(resolved.communication.tone, "direct")
        self.assertEqual(resolved.communication.verbosity, "brief")
        self.assertEqual(resolved.domain_focus.depth, "deep")

    def test_apply_calibration_skips_when_confidence_low(self) -> None:
        self._record("sig-1", tone="diplomatic", depth="overview", verbosity="detailed")

        result = self.tracker.apply_calibration(subject_id="boss", min_confidence=0.7)

        self.assertFalse(result.applied)
        resolved = self.profile_memory.resolve_profile(subject_id="boss")
        self.assertEqual(resolved.communication.tone, "balanced")

    def test_invalid_signal_raises(self) -> None:
        with self.assertRaises(CommunicationCalibrationError):
            self.tracker.record_signal(
                CommunicationCalibrationSignal(
                    signal_id="sig-invalid",
                    subject_id="boss",
                    preferred_tone="aggressive",
                    preferred_depth="deep",
                    preferred_verbosity="brief",
                    satisfaction_score=0.8,
                    created_at="2026-04-15T12:00:00Z",
                    metadata={},
                )
            )

    def test_snapshot_manifest_is_deterministic(self) -> None:
        self._record("sig-1", tone="direct", depth="deep", verbosity="brief")
        self._record("sig-2", tone="direct", depth="deep", verbosity="brief")
        self._record("sig-3", tone="direct", depth="deep", verbosity="brief")

        snapshot = self.tracker.build_snapshot(subject_id="boss")
        first = snapshot.to_manifest()
        second = snapshot.to_manifest()

        self.assertEqual(first, second)

    def _record(self, signal_id: str, *, tone: str, depth: str, verbosity: str) -> None:
        self.tracker.record_signal(
            CommunicationCalibrationSignal(
                signal_id=signal_id,
                subject_id="boss",
                preferred_tone=tone,
                preferred_depth=depth,
                preferred_verbosity=verbosity,
                satisfaction_score=0.9,
                created_at="2026-04-15T12:00:00Z",
                metadata={"source": "test"},
            )
        )


if __name__ == "__main__":
    unittest.main()
