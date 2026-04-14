"""Document and image summary extraction with explicit citations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from .ocr_layout import OCRLayoutResult
from .screenshot_pipeline import NormalizedSceneContract
from .ui_grounding import UIStateRepresentation


@dataclass(frozen=True)
class MultimodalSummaryCitation:
    citation_id: str
    source_type: str
    source_id: str
    excerpt: str
    confidence: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MultimodalSummaryResult:
    scene_id: str
    summary_id: str
    summary_text: str
    key_points: tuple[str, ...]
    citations: tuple[MultimodalSummaryCitation, ...]
    overall_confidence: float
    warnings: tuple[str, ...]
    language_hint: str | None
    generated_at: str


class MultimodalSummaryError(ValueError):
    """Raised when multimodal summary extraction receives invalid inputs."""


class DocumentImageSummaryExtractor:
    """Builds concise visual summaries with evidence citations."""

    def __init__(
        self,
        *,
        min_summary_confidence: float = 0.35,
        max_line_citations: int = 6,
        max_block_citations: int = 3,
        max_ui_citations: int = 4,
    ) -> None:
        if min_summary_confidence < 0 or min_summary_confidence > 1:
            raise MultimodalSummaryError("min_summary_confidence must be between 0 and 1")
        if max_line_citations < 1:
            raise MultimodalSummaryError("max_line_citations must be at least 1")
        if max_block_citations < 1:
            raise MultimodalSummaryError("max_block_citations must be at least 1")
        if max_ui_citations < 1:
            raise MultimodalSummaryError("max_ui_citations must be at least 1")

        self.min_summary_confidence = float(min_summary_confidence)
        self.max_line_citations = int(max_line_citations)
        self.max_block_citations = int(max_block_citations)
        self.max_ui_citations = int(max_ui_citations)

    def summarize(
        self,
        scene: NormalizedSceneContract,
        *,
        layout: OCRLayoutResult | None = None,
        ui_state: UIStateRepresentation | None = None,
        max_key_points: int = 5,
    ) -> MultimodalSummaryResult:
        _validate_scene(scene)
        if max_key_points < 1:
            raise MultimodalSummaryError("max_key_points must be at least 1")

        if layout is not None:
            _validate_layout(layout)
            if layout.scene_id != scene.scene_id:
                raise MultimodalSummaryError("layout.scene_id must match scene.scene_id")

        if ui_state is not None:
            _validate_ui_state(ui_state)
            if ui_state.scene_id != scene.scene_id:
                raise MultimodalSummaryError("ui_state.scene_id must match scene.scene_id")

        citations: list[MultimodalSummaryCitation] = []
        citation_map: dict[tuple[str, str, str], str] = {}
        warnings: list[str] = []
        key_specs: list[tuple[str, tuple[str, ...]]] = []

        def add_citation(
            source_type: str,
            source_id: str,
            excerpt: str,
            confidence: float,
            metadata: dict[str, Any],
        ) -> str:
            normalized_source_type = _normalize_required(source_type, "source_type")
            normalized_source_id = _normalize_required(source_id, "source_id")
            normalized_excerpt = _normalize_excerpt(excerpt)
            normalized_confidence = _normalize_confidence(confidence)
            key = (normalized_source_type, normalized_source_id, normalized_excerpt)
            if key in citation_map:
                return citation_map[key]

            citation_id = _hash_text(
                f"{scene.scene_id}:{normalized_source_type}:{normalized_source_id}:{normalized_excerpt}"
            )[:24]
            full_citation_id = f"sumcite-{citation_id}"
            citations.append(
                MultimodalSummaryCitation(
                    citation_id=full_citation_id,
                    source_type=normalized_source_type,
                    source_id=normalized_source_id,
                    excerpt=normalized_excerpt,
                    confidence=normalized_confidence,
                    metadata=_normalize_metadata(metadata),
                )
            )
            citation_map[key] = full_citation_id
            return full_citation_id

        scene_citation_id = add_citation(
            "scene_metadata",
            scene.scene_id,
            f"Scene {scene.normalized_width}x{scene.normalized_height} {scene.orientation} {scene.image_format}",
            1.0,
            {
                "source_id": scene.source_id,
                "source_type": scene.source_type,
                "scale_ratio": scene.scale_ratio,
                "aspect_bucket": scene.metadata.get("aspect_bucket"),
            },
        )

        line_citation_ids: dict[str, str] = {}
        block_citation_ids: dict[str, str] = {}
        ui_citation_ids: list[str] = []

        if layout is not None:
            warnings.extend(layout.warnings)

            for line in layout.lines[: self.max_line_citations]:
                line_citation_ids[line.line_id] = add_citation(
                    "ocr_line",
                    line.line_id,
                    line.text,
                    line.avg_confidence,
                    {
                        "bbox": list(line.bbox),
                        "block_id": line.block_id,
                        "span_count": len(line.span_ids),
                    },
                )

            for block in layout.blocks[: self.max_block_citations]:
                block_citation_ids[block.block_id] = add_citation(
                    "ocr_block",
                    block.block_id,
                    _trim_text(block.text, limit=220),
                    block.avg_confidence,
                    {
                        "bbox": list(block.bbox),
                        "line_count": len(block.line_ids),
                        "span_count": block.span_count,
                    },
                )

            if layout.lines:
                title_line = layout.lines[0]
                title_ref = line_citation_ids.get(title_line.line_id)
                key_specs.append(
                    (
                        f"Primary document focus: {_trim_text(title_line.text, limit=120)}.",
                        _compact_refs((title_ref,)),
                    )
                )
                top_block = layout.blocks[0] if layout.blocks else None
                top_block_ref = block_citation_ids.get(top_block.block_id) if top_block is not None else None
                key_specs.append(
                    (
                        f"Extracted {len(layout.lines)} OCR lines across {len(layout.blocks)} layout blocks.",
                        _compact_refs((top_block_ref,)),
                    )
                )

        if ui_state is not None:
            warnings.extend(ui_state.warnings)
            actionable_ids = set(ui_state.actionable_element_ids)

            actionable = [
                element for element in ui_state.elements if element.element_id in actionable_ids
            ]
            selected_elements = actionable[: self.max_ui_citations]
            if not selected_elements:
                selected_elements = list(ui_state.elements[: self.max_ui_citations])

            for element in selected_elements:
                ui_citation_ids.append(
                    add_citation(
                        "ui_element",
                        element.element_id,
                        f"{element.role}: {element.label}",
                        element.confidence,
                        {
                            "bbox": list(element.bbox),
                            "actionable": element.actionable,
                            "selector_hints": list(element.selector_hints),
                        },
                    )
                )

            if actionable:
                preview = ", ".join(element.label for element in actionable[:2])
                key_specs.append(
                    (
                        f"Detected {len(actionable)} actionable UI elements, including {preview}.",
                        _compact_refs(tuple(ui_citation_ids[:2] or ui_citation_ids)),
                    )
                )
            elif ui_state.elements:
                key_specs.append(
                    (
                        f"Detected {len(ui_state.elements)} grounded UI elements in the scene.",
                        _compact_refs(tuple(ui_citation_ids[:2] or ui_citation_ids)),
                    )
                )

            if ui_state.elements and ui_state.low_confidence_element_ids:
                ratio = len(ui_state.low_confidence_element_ids) / len(ui_state.elements)
                if ratio >= 0.4:
                    warnings.append("A large fraction of grounded UI elements are low confidence.")

        if not key_specs:
            warnings.append("No OCR or grounded UI evidence found; summary is metadata-only.")
            key_specs.append(
                (
                    "No OCR text was extracted; summary relies on image metadata and structural context.",
                    (scene_citation_id,),
                )
            )

        key_specs.append(
            (
                f"Image context: {scene.orientation} {scene.normalized_width}x{scene.normalized_height} {scene.image_format}.",
                (scene_citation_id,),
            )
        )

        trimmed_specs = key_specs[:max_key_points]
        citation_indices = {citation.citation_id: index for index, citation in enumerate(citations, start=1)}
        rendered_points = tuple(
            _render_point(text, refs, citation_indices) for text, refs in trimmed_specs
        )

        evidence_confidences = [
            citation.confidence for citation in citations if citation.source_type != "scene_metadata"
        ]
        if evidence_confidences:
            overall_confidence = round(sum(evidence_confidences) / len(evidence_confidences), 4)
        else:
            overall_confidence = 0.35

        if overall_confidence < self.min_summary_confidence:
            warnings.append(
                "Summary confidence is below recommended threshold for autonomous actioning."
            )

        unique_warnings = tuple(_unique_preserve_order(warnings))
        summary_text = "\n".join(
            f"{index}. {point}" for index, point in enumerate(rendered_points, start=1)
        )
        summary_id = _hash_text(
            f"{scene.scene_id}:{summary_text}:{','.join(citation.citation_id for citation in citations)}"
        )[:16]

        return MultimodalSummaryResult(
            scene_id=scene.scene_id,
            summary_id=summary_id,
            summary_text=summary_text,
            key_points=rendered_points,
            citations=tuple(citations),
            overall_confidence=overall_confidence,
            warnings=unique_warnings,
            language_hint=layout.language_hint if layout is not None else None,
            generated_at=_utc_now_iso(),
        )


def _validate_scene(scene: NormalizedSceneContract) -> None:
    if not isinstance(scene, NormalizedSceneContract):
        raise MultimodalSummaryError("scene must be a NormalizedSceneContract")


def _validate_layout(layout: OCRLayoutResult) -> None:
    if not isinstance(layout, OCRLayoutResult):
        raise MultimodalSummaryError("layout must be an OCRLayoutResult")


def _validate_ui_state(ui_state: UIStateRepresentation) -> None:
    if not isinstance(ui_state, UIStateRepresentation):
        raise MultimodalSummaryError("ui_state must be a UIStateRepresentation")


def _normalize_confidence(value: Any) -> float:
    normalized = float(value)
    if normalized < 0 or normalized > 1:
        raise MultimodalSummaryError("confidence must be between 0 and 1")
    return round(normalized, 4)


def _normalize_required(value: Any, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise MultimodalSummaryError(f"{field_name} is required")
    return normalized


def _normalize_excerpt(value: Any) -> str:
    return _trim_text(_normalize_required(value, "excerpt"), limit=280)


def _trim_text(value: str, *, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 3)].rstrip()}..."


def _normalize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = " ".join(str(key).split())
        if not key_text:
            continue
        normalized[key_text] = _coerce_metadata_value(value)
    return normalized


def _coerce_metadata_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_coerce_metadata_value(item) for item in value]
    if isinstance(value, tuple):
        return [_coerce_metadata_value(item) for item in value]
    if isinstance(value, dict):
        return _normalize_metadata(value)
    return str(value)


def _compact_refs(refs: tuple[str | None, ...]) -> tuple[str, ...]:
    compacted: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        compacted.append(ref)
    return tuple(compacted)


def _render_point(text: str, refs: tuple[str, ...], indices: dict[str, int]) -> str:
    markers = "".join(f"[{indices[ref]}]" for ref in refs if ref in indices)
    if markers:
        return f"{text} {markers}"
    return text


def _unique_preserve_order(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(str(value).split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "DocumentImageSummaryExtractor",
    "MultimodalSummaryCitation",
    "MultimodalSummaryError",
    "MultimodalSummaryResult",
]
