"""UI element grounding and state representation for multimodal workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from .ocr_layout import OCRLayoutLine, OCRLayoutResult
from .screenshot_pipeline import NormalizedSceneContract

_ACTIONABLE_ROLES = {
    "button",
    "link",
    "input",
    "checkbox",
    "radio",
    "switch",
    "tab",
    "menuitem",
    "dropdown",
    "combobox",
    "toggle",
    "icon_button",
}

_ROLE_ALIASES = {
    "hyperlink": "link",
    "textbox": "input",
    "text_field": "input",
    "select": "dropdown",
    "cta": "button",
}

_BUTTON_WORDS = {
    "save",
    "submit",
    "apply",
    "continue",
    "cancel",
    "delete",
    "remove",
    "start",
    "stop",
    "deploy",
    "open",
    "close",
    "next",
    "back",
    "ok",
    "confirm",
    "retry",
    "launch",
    "settings",
}


@dataclass(frozen=True)
class UIElementCandidate:
    candidate_id: str
    role: str | None
    label: str | None
    left: int
    top: int
    width: int
    height: int
    confidence: float
    selector_hints: tuple[str, ...]
    attributes: dict[str, Any]


@dataclass(frozen=True)
class UIGroundedElement:
    element_id: str
    label: str
    role: str
    bbox: tuple[int, int, int, int]
    normalized_bbox: tuple[float, float, float, float]
    center: tuple[int, int]
    confidence: float
    source_signals: tuple[str, ...]
    selector_hints: tuple[str, ...]
    text_line_ids: tuple[str, ...]
    actionable: bool
    state: dict[str, Any]


@dataclass(frozen=True)
class UIStateRepresentation:
    scene_id: str
    viewport_width: int
    viewport_height: int
    elements: tuple[UIGroundedElement, ...]
    actionable_element_ids: tuple[str, ...]
    reading_order: tuple[str, ...]
    average_confidence: float
    low_confidence_element_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    represented_at: str


class UIGroundingError(ValueError):
    """Raised when UI grounding receives invalid scene, layout, or candidate input."""


class UIGroundingModel:
    """Grounds UI elements from OCR layout and optional detector candidates."""

    def __init__(
        self,
        *,
        min_element_confidence: float = 0.45,
        iou_match_threshold: float = 0.12,
    ) -> None:
        if min_element_confidence < 0 or min_element_confidence > 1:
            raise UIGroundingError("min_element_confidence must be between 0 and 1")
        if iou_match_threshold < 0 or iou_match_threshold > 1:
            raise UIGroundingError("iou_match_threshold must be between 0 and 1")

        self.min_element_confidence = float(min_element_confidence)
        self.iou_match_threshold = float(iou_match_threshold)

    def normalize_candidates(
        self,
        candidates: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> tuple[UIElementCandidate, ...]:
        normalized: list[UIElementCandidate] = []

        for index, raw_candidate in enumerate(candidates):
            if not isinstance(raw_candidate, dict):
                raise UIGroundingError("Candidate entries must be dictionaries")

            left = _normalize_non_negative_int(raw_candidate.get("left", raw_candidate.get("x", 0)), "left")
            top = _normalize_non_negative_int(raw_candidate.get("top", raw_candidate.get("y", 0)), "top")
            width = _normalize_positive_int(raw_candidate.get("width", raw_candidate.get("w", 0)), "width")
            height = _normalize_positive_int(raw_candidate.get("height", raw_candidate.get("h", 0)), "height")
            confidence = _normalize_confidence(raw_candidate.get("confidence", raw_candidate.get("score", 0.75)))

            role = _normalize_role(
                raw_candidate.get("role", raw_candidate.get("type", raw_candidate.get("element_type"))),
                fallback=None,
            )
            label = _normalize_optional_text(raw_candidate.get("label", raw_candidate.get("text", raw_candidate.get("name"))))
            selector_hints = _normalize_selector_hints(
                raw_candidate.get("selector_hints", raw_candidate.get("selectors", raw_candidate.get("selector")))
            )

            attributes_raw = raw_candidate.get("attributes", raw_candidate.get("state", {}))
            attributes = _normalize_attributes(attributes_raw)

            candidate_id = _hash_text(
                f"candidate:{index}:{role}:{label}:{left}:{top}:{width}:{height}:{confidence:.6f}:{selector_hints}"
            )
            normalized.append(
                UIElementCandidate(
                    candidate_id=candidate_id,
                    role=role,
                    label=label,
                    left=left,
                    top=top,
                    width=width,
                    height=height,
                    confidence=confidence,
                    selector_hints=selector_hints,
                    attributes=attributes,
                )
            )

        return tuple(normalized)

    def build_state(
        self,
        scene: NormalizedSceneContract,
        layout: OCRLayoutResult,
        *,
        candidates: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    ) -> UIStateRepresentation:
        _validate_scene(scene)
        _validate_layout(layout)
        if scene.scene_id != layout.scene_id:
            raise UIGroundingError("scene.scene_id must match layout.scene_id")

        normalized_candidates = self.normalize_candidates(candidates)
        assigned_line_ids: set[str] = set()
        grounded_elements: list[UIGroundedElement] = []

        for candidate in normalized_candidates:
            candidate_bbox = (candidate.left, candidate.top, candidate.width, candidate.height)
            matched_lines = _match_lines(
                candidate_bbox,
                layout.lines,
                iou_match_threshold=self.iou_match_threshold,
            )
            assigned_line_ids.update(line.line_id for line in matched_lines)

            line_ids = tuple(line.line_id for line in matched_lines)
            merged_label = _merge_label(candidate.label, matched_lines)
            merged_role = _resolve_role(candidate.role, merged_label)
            merged_bbox = _merge_bbox(candidate_bbox, [line.bbox for line in matched_lines])
            combined_confidence = _combine_confidence(candidate.confidence, matched_lines)
            selectors = _build_selector_hints(candidate.selector_hints, merged_label, merged_role)
            state = _derive_state(candidate.attributes, label=merged_label)

            grounded_elements.append(
                _build_grounded_element(
                    scene=scene,
                    label=merged_label,
                    role=merged_role,
                    bbox=merged_bbox,
                    confidence=combined_confidence,
                    source_signals=("detector", "ocr") if matched_lines else ("detector",),
                    selector_hints=selectors,
                    text_line_ids=line_ids,
                    state=state,
                )
            )

        for line in layout.lines:
            if line.line_id in assigned_line_ids:
                continue

            label = line.text
            role = _resolve_role(None, label)
            state = _derive_state({}, label=label)
            selectors = _build_selector_hints((), label, role)

            grounded_elements.append(
                _build_grounded_element(
                    scene=scene,
                    label=label,
                    role=role,
                    bbox=line.bbox,
                    confidence=round(line.avg_confidence, 4),
                    source_signals=("ocr",),
                    selector_hints=selectors,
                    text_line_ids=(line.line_id,),
                    state=state,
                )
            )

        deduped_elements = _deduplicate_elements(grounded_elements)
        ordered_elements = tuple(sorted(deduped_elements, key=lambda item: (item.bbox[1], item.bbox[0], item.element_id)))

        reading_order = _derive_reading_order(ordered_elements, layout.reading_order)
        actionable_element_ids = tuple(
            element.element_id
            for element in ordered_elements
            if element.actionable
            and element.confidence >= self.min_element_confidence
            and _state_flag(element.state, "visible", default=True)
            and _state_flag(element.state, "enabled", default=True)
        )
        low_confidence_element_ids = tuple(
            element.element_id
            for element in ordered_elements
            if element.confidence < self.min_element_confidence
        )

        warnings = list(layout.warnings)
        if not ordered_elements:
            warnings.append("No UI elements were grounded from visual context.")
        if ordered_elements and len(low_confidence_element_ids) / len(ordered_elements) >= 0.4:
            warnings.append("Grounding confidence threshold was not met for a large portion of elements.")
        if any(element.actionable for element in ordered_elements) and not actionable_element_ids:
            warnings.append("No actionable UI element met the minimum confidence threshold.")

        average_confidence = (
            round(sum(element.confidence for element in ordered_elements) / len(ordered_elements), 4)
            if ordered_elements
            else 0.0
        )

        return UIStateRepresentation(
            scene_id=scene.scene_id,
            viewport_width=scene.normalized_width,
            viewport_height=scene.normalized_height,
            elements=ordered_elements,
            actionable_element_ids=actionable_element_ids,
            reading_order=reading_order,
            average_confidence=average_confidence,
            low_confidence_element_ids=low_confidence_element_ids,
            warnings=tuple(warnings),
            represented_at=_utc_now_iso(),
        )


def _validate_scene(scene: NormalizedSceneContract) -> None:
    if not isinstance(scene, NormalizedSceneContract):
        raise UIGroundingError("scene must be a NormalizedSceneContract")


def _validate_layout(layout: OCRLayoutResult) -> None:
    if not isinstance(layout, OCRLayoutResult):
        raise UIGroundingError("layout must be an OCRLayoutResult")


def _match_lines(
    candidate_bbox: tuple[int, int, int, int],
    lines: tuple[OCRLayoutLine, ...],
    *,
    iou_match_threshold: float,
) -> tuple[OCRLayoutLine, ...]:
    matched = [
        line
        for line in lines
        if _iou(candidate_bbox, line.bbox) >= iou_match_threshold or _contains_bbox(candidate_bbox, line.bbox)
    ]
    matched.sort(key=lambda line: (line.bbox[1], line.bbox[0], line.line_id))
    return tuple(matched)


def _merge_label(candidate_label: str | None, matched_lines: tuple[OCRLayoutLine, ...]) -> str:
    if candidate_label:
        return candidate_label
    if matched_lines:
        return " ".join(line.text for line in matched_lines).strip()
    return "unlabeled_element"


def _resolve_role(candidate_role: str | None, label: str) -> str:
    if candidate_role is not None:
        normalized = _ROLE_ALIASES.get(candidate_role, candidate_role)
        return normalized

    lowered = label.lower()
    if "http://" in lowered or "https://" in lowered or "www." in lowered:
        return "link"

    words = [word for word in lowered.replace("/", " ").split() if word]
    if words and words[0] in _BUTTON_WORDS and len(words) <= 4:
        return "button"
    if any(word in _BUTTON_WORDS for word in words) and len(words) <= 3:
        return "button"
    if any(token in lowered for token in ("toggle", "switch")):
        return "switch"
    if "checkbox" in lowered:
        return "checkbox"
    if "radio" in lowered:
        return "radio"
    if "input" in lowered or "type here" in lowered:
        return "input"
    return "text"


def _merge_bbox(
    candidate_bbox: tuple[int, int, int, int],
    line_bboxes: list[tuple[int, int, int, int]],
) -> tuple[int, int, int, int]:
    if not line_bboxes:
        return candidate_bbox

    all_bboxes = [candidate_bbox, *line_bboxes]
    left = min(bbox[0] for bbox in all_bboxes)
    top = min(bbox[1] for bbox in all_bboxes)
    right = max(bbox[0] + bbox[2] for bbox in all_bboxes)
    bottom = max(bbox[1] + bbox[3] for bbox in all_bboxes)
    return (left, top, right - left, bottom - top)


def _combine_confidence(candidate_confidence: float, matched_lines: tuple[OCRLayoutLine, ...]) -> float:
    if not matched_lines:
        return round(candidate_confidence, 4)

    line_confidence = sum(line.avg_confidence for line in matched_lines) / len(matched_lines)
    combined = (candidate_confidence * 0.60) + (line_confidence * 0.40)
    return round(_clamp(combined), 4)


def _build_selector_hints(
    candidate_selectors: tuple[str, ...],
    label: str,
    role: str,
) -> tuple[str, ...]:
    selectors: list[str] = list(candidate_selectors)
    if label and len(label) <= 80:
        selectors.append(f"text={label}")
    if role != "text":
        selectors.append(f"role={role}")

    deduped: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        normalized = " ".join(selector.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return tuple(deduped)


def _derive_state(attributes: dict[str, Any], *, label: str) -> dict[str, Any]:
    state = {
        "visible": True,
        "enabled": True,
        "selected": False,
    }

    for key, value in attributes.items():
        normalized_key = " ".join(str(key).split()).lower()
        if not normalized_key:
            continue
        state[normalized_key] = _coerce_attribute_value(value)

    lowered_label = label.lower()
    if "disabled" in lowered_label or "unavailable" in lowered_label or "locked" in lowered_label:
        state["enabled"] = False
    if "selected" in lowered_label or "active" in lowered_label:
        state["selected"] = True

    return state


def _build_grounded_element(
    *,
    scene: NormalizedSceneContract,
    label: str,
    role: str,
    bbox: tuple[int, int, int, int],
    confidence: float,
    source_signals: tuple[str, ...],
    selector_hints: tuple[str, ...],
    text_line_ids: tuple[str, ...],
    state: dict[str, Any],
) -> UIGroundedElement:
    left, top, width, height = bbox
    normalized_bbox = (
        round(left / scene.normalized_width, 6),
        round(top / scene.normalized_height, 6),
        round(width / scene.normalized_width, 6),
        round(height / scene.normalized_height, 6),
    )
    center = (left + (width // 2), top + (height // 2))
    actionable = role in _ACTIONABLE_ROLES

    element_id = _hash_text(
        f"{scene.scene_id}:{label}:{role}:{bbox}:{source_signals}:{selector_hints}:{text_line_ids}"
    )

    return UIGroundedElement(
        element_id=element_id,
        label=label,
        role=role,
        bbox=bbox,
        normalized_bbox=normalized_bbox,
        center=center,
        confidence=round(_clamp(confidence), 4),
        source_signals=source_signals,
        selector_hints=selector_hints,
        text_line_ids=text_line_ids,
        actionable=actionable,
        state=state,
    )


def _deduplicate_elements(elements: list[UIGroundedElement]) -> tuple[UIGroundedElement, ...]:
    chosen: dict[tuple[str, str, tuple[int, int, int, int]], UIGroundedElement] = {}
    for element in elements:
        dedupe_key = (element.label.lower(), element.role, element.bbox)
        current = chosen.get(dedupe_key)
        if current is None or element.confidence > current.confidence:
            chosen[dedupe_key] = element
    return tuple(chosen.values())


def _derive_reading_order(
    elements: tuple[UIGroundedElement, ...],
    line_reading_order: tuple[str, ...],
) -> tuple[str, ...]:
    by_line_id: dict[str, str] = {}
    for element in elements:
        for line_id in element.text_line_ids:
            by_line_id.setdefault(line_id, element.element_id)

    ordered: list[str] = []
    for line_id in line_reading_order:
        element_id = by_line_id.get(line_id)
        if element_id and element_id not in ordered:
            ordered.append(element_id)

    for element in elements:
        if element.element_id not in ordered:
            ordered.append(element.element_id)

    return tuple(ordered)


def _state_flag(state: dict[str, Any], key: str, *, default: bool) -> bool:
    value = state.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _normalize_non_negative_int(value: Any, field_name: str) -> int:
    normalized = int(value)
    if normalized < 0:
        raise UIGroundingError(f"{field_name} must be non-negative")
    return normalized


def _normalize_positive_int(value: Any, field_name: str) -> int:
    normalized = int(value)
    if normalized < 1:
        raise UIGroundingError(f"{field_name} must be at least 1")
    return normalized


def _normalize_confidence(value: Any) -> float:
    normalized = float(value)
    if normalized < 0 or normalized > 1:
        raise UIGroundingError("confidence must be between 0 and 1")
    return normalized


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    if not normalized:
        return None
    return normalized


def _normalize_role(value: Any, *, fallback: str | None) -> str | None:
    if value is None:
        return fallback

    normalized = "_".join(str(value).split()).lower()
    if not normalized:
        return fallback
    return _ROLE_ALIASES.get(normalized, normalized)


def _normalize_selector_hints(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()

    raw_items: list[Any]
    if isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raw_items = [value]

    selectors: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        normalized = " ".join(str(item).split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        selectors.append(normalized)
    return tuple(selectors)


def _normalize_attributes(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise UIGroundingError("attributes must be a dictionary when provided")

    normalized: dict[str, Any] = {}
    for key, raw_value in value.items():
        normalized_key = " ".join(str(key).split())
        if not normalized_key:
            continue
        normalized[normalized_key] = _coerce_attribute_value(raw_value)
    return normalized


def _coerce_attribute_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {" ".join(str(k).split()): _coerce_attribute_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_attribute_value(item) for item in value]
    if isinstance(value, tuple):
        return [_coerce_attribute_value(item) for item in value]
    return str(value)


def _contains_bbox(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    outer_left, outer_top, outer_width, outer_height = outer
    inner_left, inner_top, inner_width, inner_height = inner

    outer_right = outer_left + outer_width
    outer_bottom = outer_top + outer_height
    inner_right = inner_left + inner_width
    inner_bottom = inner_top + inner_height

    return (
        inner_left >= outer_left
        and inner_top >= outer_top
        and inner_right <= outer_right
        and inner_bottom <= outer_bottom
    )


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    a_left, a_top, a_width, a_height = a
    b_left, b_top, b_width, b_height = b

    a_right = a_left + a_width
    a_bottom = a_top + a_height
    b_right = b_left + b_width
    b_bottom = b_top + b_height

    inter_left = max(a_left, b_left)
    inter_top = max(a_top, b_top)
    inter_right = min(a_right, b_right)
    inter_bottom = min(a_bottom, b_bottom)

    inter_width = max(0, inter_right - inter_left)
    inter_height = max(0, inter_bottom - inter_top)
    inter_area = inter_width * inter_height
    if inter_area == 0:
        return 0.0

    a_area = a_width * a_height
    b_area = b_width * b_height
    union_area = a_area + b_area - inter_area
    if union_area <= 0:
        return 0.0

    return inter_area / float(union_area)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "UIElementCandidate",
    "UIGroundedElement",
    "UIGroundingError",
    "UIGroundingModel",
    "UIStateRepresentation",
]
