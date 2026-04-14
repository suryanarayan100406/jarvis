"""Voice latency and reliability tests for noisy-input conditions (P3-T12)."""

from __future__ import annotations

import unittest
from time import perf_counter

from runtime.voice import StreamingSttAdapter, StreamingTtsAdapter, WakePhraseDetector

WAKE_LATENCY_BUDGET_SECONDS = 0.1
ROUND_TRIP_BUDGET_SECONDS = 0.2


class VoiceLatencyReliabilityTests(unittest.TestCase):
    def test_wake_detection_latency_under_noise_budget(self) -> None:
        detector = WakePhraseDetector()
        noisy_chunks = [
            "static hiss crackle",
            "background fan noise",
            "random telemetry words",
        ] * 20
        stream = noisy_chunks + ["hey friday run diagnostics"]

        start = perf_counter()
        detection = None
        for chunk in stream:
            event = detector.process_chunk(chunk)
            if event is not None:
                detection = event
                break
        elapsed = perf_counter() - start

        self.assertIsNotNone(detection)
        assert detection is not None
        self.assertEqual(detection.wake_phrase, "hey friday")
        self.assertLess(
            elapsed,
            WAKE_LATENCY_BUDGET_SECONDS,
            msg=(
                f"Wake detection exceeded latency budget: elapsed={elapsed:.6f}s "
                f"budget={WAKE_LATENCY_BUDGET_SECONDS:.6f}s"
            ),
        )

    def test_noise_does_not_trigger_false_wake(self) -> None:
        detector = WakePhraseDetector()
        stream = [
            "hay frida monitor system",
            "hey frida check logs",
            "noise only no command",
            "jarviss standby",
        ]

        detections = [detector.process_chunk(chunk) for chunk in stream]

        self.assertEqual([item for item in detections if item is not None], [])

    def test_stt_reliability_with_noisy_chunks(self) -> None:
        adapter = StreamingSttAdapter()
        chunks = [
            b"\x00\xff\x00",
            b"  static  ",
            b"hey ",
            b"friday",
        ]

        frames = list(adapter.transcribe_stream(chunks))

        self.assertGreaterEqual(len(frames), 2)
        self.assertTrue(frames[-1].is_final)
        self.assertIn("hey friday", frames[-1].text)

    def test_voice_round_trip_latency_budget(self) -> None:
        detector = WakePhraseDetector()
        stt = StreamingSttAdapter()
        tts = StreamingTtsAdapter(max_chars_per_chunk=32)

        start = perf_counter()
        transcript_frames = list(stt.transcribe_stream([b"hey ", b"friday", b" run diagnostics"]))
        final_transcript = transcript_frames[-1].text
        detection = detector.detect(final_transcript)
        audio_chunks = list(tts.synthesize_stream("Diagnostics initiated"))
        elapsed = perf_counter() - start

        self.assertIsNotNone(detection)
        assert detection is not None
        self.assertEqual(detection.wake_phrase, "hey friday")
        self.assertGreaterEqual(len(audio_chunks), 1)
        self.assertLess(
            elapsed,
            ROUND_TRIP_BUDGET_SECONDS,
            msg=(
                f"Voice round trip exceeded latency budget: elapsed={elapsed:.6f}s "
                f"budget={ROUND_TRIP_BUDGET_SECONDS:.6f}s"
            ),
        )


if __name__ == "__main__":
    unittest.main()
