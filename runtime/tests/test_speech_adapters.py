"""Tests for P3-T2 streaming speech adapters."""

from __future__ import annotations

import unittest

from runtime.voice import StreamingSttAdapter, StreamingTtsAdapter


class StreamingSttAdapterTests(unittest.TestCase):
    def test_transcribe_stream_produces_progressive_and_final_frames(self) -> None:
        adapter = StreamingSttAdapter()

        frames = list(adapter.transcribe_stream([b"hello ", b"boss"]))

        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0].text, "hello")
        self.assertFalse(frames[0].is_final)
        self.assertEqual(frames[1].text, "hello boss")
        self.assertFalse(frames[1].is_final)
        self.assertEqual(frames[2].text, "hello boss")
        self.assertTrue(frames[2].is_final)

    def test_transcribe_stream_ignores_empty_or_whitespace_chunks(self) -> None:
        adapter = StreamingSttAdapter()

        frames = list(adapter.transcribe_stream([b"", b"   ", b"\n\t"]))

        self.assertEqual(frames, [])

    def test_custom_decoder_is_used(self) -> None:
        adapter = StreamingSttAdapter(decoder=lambda _chunk: "agent ready")

        frames = list(adapter.transcribe_stream([b"ignored"]))

        self.assertEqual(frames[-1].text, "agent ready")
        self.assertTrue(frames[-1].is_final)


class StreamingTtsAdapterTests(unittest.TestCase):
    def test_synthesize_stream_chunks_text_with_sequence_order(self) -> None:
        adapter = StreamingTtsAdapter(max_chars_per_chunk=12)

        chunks = list(adapter.synthesize_stream("status report is ready now"))

        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual([chunk.sequence_id for chunk in chunks], list(range(len(chunks))))
        self.assertTrue(all(len(chunk.text) <= 12 for chunk in chunks))
        self.assertEqual(" ".join(chunk.text for chunk in chunks), "status report is ready now")

    def test_custom_encoder_is_used(self) -> None:
        adapter = StreamingTtsAdapter(encoder=lambda text: text.upper().encode("utf-8"), max_chars_per_chunk=64)

        chunks = list(adapter.synthesize_stream("hello boss"))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].audio, b"HELLO BOSS")

    def test_empty_text_produces_no_chunks(self) -> None:
        adapter = StreamingTtsAdapter()

        chunks = list(adapter.synthesize_stream("   \n  \t"))

        self.assertEqual(chunks, [])


if __name__ == "__main__":
    unittest.main()
