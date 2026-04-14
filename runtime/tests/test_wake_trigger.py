"""Tests for P3-T1 wake trigger local phrase detection."""

from __future__ import annotations

import unittest

from runtime.voice import WakePhraseDetector


class WakePhraseDetectorTests(unittest.TestCase):
    def test_detects_default_phrase_with_case_and_punctuation(self) -> None:
        detector = WakePhraseDetector()

        detection = detector.detect("Please, HEY JARVIS, give me status.")

        self.assertIsNotNone(detection)
        assert detection is not None
        self.assertEqual(detection.wake_phrase, "hey jarvis")
        self.assertEqual(detection.confidence, 1.0)

    def test_returns_none_when_no_wake_phrase_present(self) -> None:
        detector = WakePhraseDetector()

        detection = detector.detect("Please continue monitoring silently")

        self.assertIsNone(detection)

    def test_streaming_detection_handles_chunk_boundaries(self) -> None:
        detector = WakePhraseDetector()

        self.assertIsNone(detector.process_chunk("can you"))
        self.assertIsNone(detector.process_chunk("hey"))
        detection = detector.process_chunk("friday open diagnostics")

        self.assertIsNotNone(detection)
        assert detection is not None
        self.assertEqual(detection.wake_phrase, "hey friday")

    def test_streaming_detection_avoids_duplicate_triggers(self) -> None:
        detector = WakePhraseDetector()

        first = detector.process_chunk("hey friday")
        second = detector.process_chunk("please proceed")
        third = detector.process_chunk("ok jarvis")

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(third)
        assert third is not None
        self.assertEqual(third.wake_phrase, "ok jarvis")

    def test_custom_phrase_support(self) -> None:
        detector = WakePhraseDetector(wake_phrases=["computer"], min_confidence=1.0)

        detection = detector.detect("Computer, enable mission brief")

        self.assertIsNotNone(detection)
        assert detection is not None
        self.assertEqual(detection.wake_phrase, "computer")

    def test_reset_clears_streaming_state(self) -> None:
        detector = WakePhraseDetector()
        detector.process_chunk("hey friday")

        detector.reset()
        detection = detector.process_chunk("noise only")

        self.assertIsNone(detection)


if __name__ == "__main__":
    unittest.main()
