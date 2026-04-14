"""Local wake trigger phrase detection for streaming text input."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


@dataclass(frozen=True)
class WakeDetection:
    wake_phrase: str
    matched_phrase: str
    confidence: float
    start_token_index: int
    end_token_index: int
    detected_at: str


class WakePhraseDetector:
    """Detects configured wake phrases from one-shot text or streaming chunks."""

    def __init__(
        self,
        wake_phrases: Iterable[str] | None = None,
        *,
        min_confidence: float = 1.0,
        max_buffer_tokens: int = 128,
    ) -> None:
        if min_confidence <= 0 or min_confidence > 1:
            raise ValueError("min_confidence must be in range (0, 1]")
        if max_buffer_tokens < 1:
            raise ValueError("max_buffer_tokens must be at least 1")

        phrases = list(wake_phrases) if wake_phrases is not None else [
            "hey friday",
            "ok friday",
            "hey jarvis",
            "ok jarvis",
        ]

        self._phrase_map = _normalize_phrases(phrases)
        if not self._phrase_map:
            raise ValueError("At least one valid wake phrase is required")

        self.min_confidence = min_confidence
        self.max_buffer_tokens = max_buffer_tokens

        self._tokens: list[str] = []
        self._base_token_index = 0
        self._last_emitted_end_index = -1

    def reset(self) -> None:
        self._tokens = []
        self._base_token_index = 0
        self._last_emitted_end_index = -1

    def detect(self, text: str) -> WakeDetection | None:
        tokens = _tokenize(text)
        if not tokens:
            return None
        return self._find_best_match(tokens=tokens, offset=0, min_end_index=None)

    def process_chunk(self, chunk: str) -> WakeDetection | None:
        chunk_tokens = _tokenize(chunk)
        if not chunk_tokens:
            return None

        self._tokens.extend(chunk_tokens)
        self._trim_buffer()

        detection = self._find_best_match(
            tokens=self._tokens,
            offset=self._base_token_index,
            min_end_index=self._last_emitted_end_index + 1,
        )

        if detection is not None:
            self._last_emitted_end_index = detection.end_token_index

        return detection

    def _trim_buffer(self) -> None:
        overflow = len(self._tokens) - self.max_buffer_tokens
        if overflow > 0:
            del self._tokens[:overflow]
            self._base_token_index += overflow

    def _find_best_match(
        self,
        *,
        tokens: list[str],
        offset: int,
        min_end_index: int | None,
    ) -> WakeDetection | None:
        best: WakeDetection | None = None

        for wake_phrase, phrase_tokens in self._phrase_map.items():
            phrase_len = len(phrase_tokens)
            if phrase_len > len(tokens):
                continue

            for index in range(0, len(tokens) - phrase_len + 1):
                window = tokens[index : index + phrase_len]
                confidence = _token_match_confidence(window, phrase_tokens)
                if confidence < self.min_confidence:
                    continue

                start_index = offset + index
                end_index = start_index + phrase_len - 1
                if min_end_index is not None and end_index < min_end_index:
                    continue

                candidate = WakeDetection(
                    wake_phrase=wake_phrase,
                    matched_phrase=" ".join(window),
                    confidence=round(confidence, 3),
                    start_token_index=start_index,
                    end_token_index=end_index,
                    detected_at=_utc_now_iso(),
                )

                if _is_better_candidate(candidate, best):
                    best = candidate

        return best


def _tokenize(text: str) -> list[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return [token for token in normalized.split() if token]


def _normalize_phrases(phrases: list[str]) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for phrase in phrases:
        tokens = _tokenize(phrase)
        if tokens:
            normalized[" ".join(tokens)] = tokens
    return normalized


def _token_match_confidence(window: list[str], target: list[str]) -> float:
    matches = sum(1 for source, expected in zip(window, target) if source == expected)
    return matches / len(target)


def _is_better_candidate(candidate: WakeDetection, current: WakeDetection | None) -> bool:
    if current is None:
        return True
    if candidate.confidence > current.confidence:
        return True
    if candidate.confidence < current.confidence:
        return False
    return candidate.start_token_index < current.start_token_index


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
