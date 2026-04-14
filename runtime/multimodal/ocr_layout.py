"""OCR and layout analysis utilities for text-rich interface captures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from .screenshot_pipeline import NormalizedSceneContract


@dataclass(frozen=True)
class OCRTextSpan:
    span_id: str
    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float
    line_id: str
    block_id: str


@dataclass(frozen=True)
class OCRLayoutLine:
    line_id: str
    block_id: str
    text: str
    span_ids: tuple[str, ...]
    bbox: tuple[int, int, int, int]
    avg_confidence: float


@dataclass(frozen=True)
class OCRLayoutBlock:
    block_id: str
    text: str
    line_ids: tuple[str, ...]
    span_count: int
    bbox: tuple[int, int, int, int]
    avg_confidence: float


@dataclass(frozen=True)
class OCRLayoutResult:
    scene_id: str
    full_text: str
    language_hint: str | None
    spans: tuple[OCRTextSpan, ...]
    lines: tuple[OCRLayoutLine, ...]
    blocks: tuple[OCRLayoutBlock, ...]
    reading_order: tuple[str, ...]
    avg_confidence: float
    low_confidence_ratio: float
    warnings: tuple[str, ...]
    analyzed_at: str


class OCRLayoutError(ValueError):
    """Raised when OCR parsing or layout analysis receives invalid input."""


class OCRLayoutAnalyzer:
    """Parses OCR payloads and derives normalized line or block layout contracts."""

    def __init__(self, *, min_confidence: float = 0.35) -> None:
        if min_confidence < 0 or min_confidence > 1:
            raise OCRLayoutError("min_confidence must be between 0 and 1")
        self.min_confidence = float(min_confidence)

    def parse_ocr_payload(
        self,
        scene: NormalizedSceneContract,
        payload: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> tuple[OCRTextSpan, ...]:
        """Normalize OCR engine payload into typed text spans."""
        _validate_scene(scene)

        spans: list[OCRTextSpan] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise OCRLayoutError("OCR payload entries must be dictionaries")

            text = _normalize_text(item.get("text", ""))
            if not text:
                continue

            left = _normalize_non_negative_int(item.get("left", item.get("x", 0)), "left")
            top = _normalize_non_negative_int(item.get("top", item.get("y", 0)), "top")
            width = _normalize_positive_int(item.get("width", item.get("w", 0)), "width")
            height = _normalize_positive_int(item.get("height", item.get("h", 0)), "height")
            confidence = _normalize_confidence(item.get("confidence", 1.0))

            line_id = _normalize_optional_id(item.get("line_id", item.get("line")), fallback=f"line:{index + 1}")
            block_id = _normalize_optional_id(item.get("block_id", item.get("block")), fallback=f"block:{line_id}")

            span_id = _hash_text(
                f"{scene.scene_id}:{index}:{text}:{left}:{top}:{width}:{height}:{line_id}:{block_id}"
            )
            spans.append(
                OCRTextSpan(
                    span_id=span_id,
                    text=text,
                    left=left,
                    top=top,
                    width=width,
                    height=height,
                    confidence=confidence,
                    line_id=line_id,
                    block_id=block_id,
                )
            )

        return tuple(spans)

    def analyze_payload(
        self,
        scene: NormalizedSceneContract,
        payload: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        *,
        language_hint: str | None = None,
    ) -> OCRLayoutResult:
        """Parse OCR payload then compute normalized layout aggregates."""
        spans = self.parse_ocr_payload(scene, payload)
        return self.analyze(scene, spans, language_hint=language_hint)

    def analyze(
        self,
        scene: NormalizedSceneContract,
        spans: list[OCRTextSpan] | tuple[OCRTextSpan, ...],
        *,
        language_hint: str | None = None,
    ) -> OCRLayoutResult:
        """Analyze normalized OCR spans and derive read order plus confidence metrics."""
        _validate_scene(scene)

        ordered_spans = tuple(sorted(spans, key=lambda span: (span.top, span.left, span.span_id)))
        if not ordered_spans:
            return OCRLayoutResult(
                scene_id=scene.scene_id,
                full_text="",
                language_hint=_normalize_optional_id(language_hint, fallback=None),
                spans=(),
                lines=(),
                blocks=(),
                reading_order=(),
                avg_confidence=0.0,
                low_confidence_ratio=0.0,
                warnings=("No OCR text spans were available for layout analysis.",),
                analyzed_at=_utc_now_iso(),
            )

        line_groups: dict[str, list[OCRTextSpan]] = {}
        for span in ordered_spans:
            line_groups.setdefault(span.line_id, []).append(span)

        lines: list[OCRLayoutLine] = []
        for line_id, line_spans in line_groups.items():
            line_spans.sort(key=lambda item: (item.top, item.left, item.span_id))
            line_text = " ".join(span.text for span in line_spans)
            bbox = _bbox_for_spans(line_spans)
            avg_confidence = round(sum(span.confidence for span in line_spans) / len(line_spans), 4)
            lines.append(
                OCRLayoutLine(
                    line_id=line_id,
                    block_id=line_spans[0].block_id,
                    text=line_text,
                    span_ids=tuple(span.span_id for span in line_spans),
                    bbox=bbox,
                    avg_confidence=avg_confidence,
                )
            )

        lines.sort(key=lambda line: (line.bbox[1], line.bbox[0], line.line_id))

        block_groups: dict[str, list[OCRLayoutLine]] = {}
        for line in lines:
            block_groups.setdefault(line.block_id, []).append(line)

        blocks: list[OCRLayoutBlock] = []
        for block_id, block_lines in block_groups.items():
            block_lines.sort(key=lambda item: (item.bbox[1], item.bbox[0], item.line_id))
            block_text = "\n".join(line.text for line in block_lines)
            bbox = _bbox_for_lines(block_lines)
            span_count = sum(len(line.span_ids) for line in block_lines)
            total_confidence = sum(line.avg_confidence * len(line.span_ids) for line in block_lines)
            avg_confidence = round(total_confidence / span_count, 4) if span_count else 0.0
            blocks.append(
                OCRLayoutBlock(
                    block_id=block_id,
                    text=block_text,
                    line_ids=tuple(line.line_id for line in block_lines),
                    span_count=span_count,
                    bbox=bbox,
                    avg_confidence=avg_confidence,
                )
            )

        blocks.sort(key=lambda block: (block.bbox[1], block.bbox[0], block.block_id))

        full_text = "\n".join(line.text for line in lines)
        low_confidence_count = sum(1 for span in ordered_spans if span.confidence < self.min_confidence)
        low_confidence_ratio = round(low_confidence_count / len(ordered_spans), 4)
        avg_confidence = round(sum(span.confidence for span in ordered_spans) / len(ordered_spans), 4)

        warnings: list[str] = []
        if low_confidence_ratio >= 0.4:
            warnings.append("High low-confidence span ratio detected in OCR output.")
        if avg_confidence < 0.5:
            warnings.append("Average OCR confidence is below recommended threshold.")

        normalized_language = _normalize_optional_id(language_hint, fallback=None)
        return OCRLayoutResult(
            scene_id=scene.scene_id,
            full_text=full_text,
            language_hint=normalized_language,
            spans=ordered_spans,
            lines=tuple(lines),
            blocks=tuple(blocks),
            reading_order=tuple(line.line_id for line in lines),
            avg_confidence=avg_confidence,
            low_confidence_ratio=low_confidence_ratio,
            warnings=tuple(warnings),
            analyzed_at=_utc_now_iso(),
        )


def _validate_scene(scene: NormalizedSceneContract) -> None:
    if not isinstance(scene, NormalizedSceneContract):
        raise OCRLayoutError("scene must be a NormalizedSceneContract")


def _bbox_for_spans(spans: list[OCRTextSpan]) -> tuple[int, int, int, int]:
    min_left = min(span.left for span in spans)
    min_top = min(span.top for span in spans)
    max_right = max(span.left + span.width for span in spans)
    max_bottom = max(span.top + span.height for span in spans)
    return (min_left, min_top, max_right - min_left, max_bottom - min_top)


def _bbox_for_lines(lines: list[OCRLayoutLine]) -> tuple[int, int, int, int]:
    min_left = min(line.bbox[0] for line in lines)
    min_top = min(line.bbox[1] for line in lines)
    max_right = max(line.bbox[0] + line.bbox[2] for line in lines)
    max_bottom = max(line.bbox[1] + line.bbox[3] for line in lines)
    return (min_left, min_top, max_right - min_left, max_bottom - min_top)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value).split())


def _normalize_non_negative_int(value: Any, field_name: str) -> int:
    normalized = int(value)
    if normalized < 0:
        raise OCRLayoutError(f"{field_name} must be non-negative")
    return normalized


def _normalize_positive_int(value: Any, field_name: str) -> int:
    normalized = int(value)
    if normalized < 1:
        raise OCRLayoutError(f"{field_name} must be at least 1")
    return normalized


def _normalize_confidence(value: Any) -> float:
    normalized = float(value)
    if normalized < 0 or normalized > 1:
        raise OCRLayoutError("confidence must be between 0 and 1")
    return normalized


def _normalize_optional_id(value: Any, *, fallback: str | None) -> str | None:
    if value is None:
        return fallback
    normalized = " ".join(str(value).split())
    if not normalized:
        return fallback
    return normalized


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "OCRLayoutAnalyzer",
    "OCRLayoutBlock",
    "OCRLayoutError",
    "OCRLayoutLine",
    "OCRLayoutResult",
    "OCRTextSpan",
]
