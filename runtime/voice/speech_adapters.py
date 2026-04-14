"""Local streaming speech adapters for text transcription and synthesis pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Iterator

SttDecoder = Callable[[bytes], str]
TtsEncoder = Callable[[str], bytes]


@dataclass(frozen=True)
class SpeechFrame:
    sequence_id: int
    text: str
    is_final: bool
    confidence: float
    timestamp: str


@dataclass(frozen=True)
class TtsChunk:
    sequence_id: int
    text: str
    audio: bytes
    sample_rate_hz: int
    mime_type: str


class StreamingSttAdapter:
    """Converts streaming audio chunks into progressive transcript frames."""

    def __init__(self, decoder: SttDecoder | None = None) -> None:
        self.decoder = decoder or self._default_decoder

    def transcribe_stream(self, chunks: Iterable[bytes]) -> Iterator[SpeechFrame]:
        sequence_id = 0
        transcript = ""

        for chunk in chunks:
            if not chunk:
                continue

            decoded = self.decoder(chunk)
            normalized = _normalize_text(decoded)
            if not normalized:
                continue

            transcript = _normalize_text(f"{transcript} {normalized}")
            yield SpeechFrame(
                sequence_id=sequence_id,
                text=transcript,
                is_final=False,
                confidence=0.85,
                timestamp=_utc_now_iso(),
            )
            sequence_id += 1

        if transcript:
            yield SpeechFrame(
                sequence_id=sequence_id,
                text=transcript,
                is_final=True,
                confidence=1.0,
                timestamp=_utc_now_iso(),
            )

    @staticmethod
    def _default_decoder(chunk: bytes) -> str:
        return chunk.decode("utf-8", errors="ignore")


class StreamingTtsAdapter:
    """Converts text responses into streamable synthesized audio chunks."""

    def __init__(
        self,
        encoder: TtsEncoder | None = None,
        *,
        max_chars_per_chunk: int = 120,
        sample_rate_hz: int = 16000,
        mime_type: str = "audio/raw",
    ) -> None:
        if max_chars_per_chunk < 1:
            raise ValueError("max_chars_per_chunk must be at least 1")
        if sample_rate_hz < 1:
            raise ValueError("sample_rate_hz must be at least 1")

        self.encoder = encoder or self._default_encoder
        self.max_chars_per_chunk = max_chars_per_chunk
        self.sample_rate_hz = sample_rate_hz
        self.mime_type = mime_type

    def synthesize_stream(self, text: str) -> Iterator[TtsChunk]:
        normalized = _normalize_text(text)
        if not normalized:
            return

        for sequence_id, segment in enumerate(_segment_text(normalized, self.max_chars_per_chunk)):
            encoded = self.encoder(segment)
            if not isinstance(encoded, (bytes, bytearray)):
                raise TypeError("encoder must return bytes")

            yield TtsChunk(
                sequence_id=sequence_id,
                text=segment,
                audio=bytes(encoded),
                sample_rate_hz=self.sample_rate_hz,
                mime_type=self.mime_type,
            )

    @staticmethod
    def _default_encoder(text: str) -> bytes:
        return text.encode("utf-8")


def _segment_text(text: str, max_chars: int) -> list[str]:
    words = text.split()
    segments: list[str] = []
    current = ""

    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            segments.append(current)
        current = word

    if current:
        segments.append(current)

    return segments


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
