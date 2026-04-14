"""Before and after UI state validation for critical UI tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from .ui_grounding import UIGroundedElement

_DESTRUCTIVE_INTENTS = {
    "delete",
    "remove",
    "wipe",
    "drop",
    "destroy",
    "disable",
}


@dataclass(frozen=True)
class UIElementStateSnapshot:
    snapshot_id: str
    task_id: str
    scene_id: str
    element_id: str
    label: str
    role: str
    bbox: tuple[int, int, int, int]
    normalized_bbox: tuple[float, float, float, float]
    visible: bool
    enabled: bool
    selected: bool
    confidence: float
    selector_hints: tuple[str, ...]
    captured_at: str


@dataclass(frozen=True)
class UIStateValidationResult:
    task_id: str
    phase: str
    passed: bool
    reason: str
    details: dict[str, Any]


class UIStateValidatorError(ValueError):
    """Raised when critical UI state validation receives invalid inputs."""


class CriticalUIStateValidator:
    """Validates before and after state invariants for critical UI actions."""

    def __init__(self, *, min_after_confidence: float = 0.3, min_stable_iou: float = 0.08) -> None:
        if min_after_confidence < 0 or min_after_confidence > 1:
            raise UIStateValidatorError("min_after_confidence must be between 0 and 1")
        if min_stable_iou < 0 or min_stable_iou > 1:
            raise UIStateValidatorError("min_stable_iou must be between 0 and 1")

        self.min_after_confidence = float(min_after_confidence)
        self.min_stable_iou = float(min_stable_iou)

    def capture_before_snapshot(
        self,
        *,
        task_id: str,
        scene_id: str,
        element: UIGroundedElement,
    ) -> UIElementStateSnapshot:
        normalized_task_id = _normalize_required(task_id, "task_id")
        normalized_scene_id = _normalize_required(scene_id, "scene_id")
        _validate_element(element)

        snapshot_id = _hash_text(f"{normalized_task_id}:{normalized_scene_id}:{element.element_id}:{element.confidence}")
        return UIElementStateSnapshot(
            snapshot_id=snapshot_id,
            task_id=normalized_task_id,
            scene_id=normalized_scene_id,
            element_id=element.element_id,
            label=element.label,
            role=element.role,
            bbox=element.bbox,
            normalized_bbox=element.normalized_bbox,
            visible=_state_flag(element.state, "visible", default=True),
            enabled=_state_flag(element.state, "enabled", default=True),
            selected=_state_flag(element.state, "selected", default=False),
            confidence=element.confidence,
            selector_hints=element.selector_hints,
            captured_at=_utc_now_iso(),
        )

    def validate_before(
        self,
        *,
        task_id: str,
        intent: str | None,
        element: UIGroundedElement | None,
    ) -> UIStateValidationResult:
        normalized_task_id = _normalize_required(task_id, "task_id")
        normalized_intent = _normalize_optional_text(intent, fallback="click")

        if element is None:
            return UIStateValidationResult(
                task_id=normalized_task_id,
                phase="before",
                passed=False,
                reason="critical_before_validation_missing_target",
                details={"intent": normalized_intent},
            )

        if not _state_flag(element.state, "visible", default=True):
            return UIStateValidationResult(
                task_id=normalized_task_id,
                phase="before",
                passed=False,
                reason="critical_before_validation_target_not_visible",
                details={"element_id": element.element_id, "intent": normalized_intent},
            )

        if not _state_flag(element.state, "enabled", default=True):
            return UIStateValidationResult(
                task_id=normalized_task_id,
                phase="before",
                passed=False,
                reason="critical_before_validation_target_disabled",
                details={"element_id": element.element_id, "intent": normalized_intent},
            )

        return UIStateValidationResult(
            task_id=normalized_task_id,
            phase="before",
            passed=True,
            reason="critical_before_validation_passed",
            details={
                "element_id": element.element_id,
                "intent": normalized_intent,
                "confidence": element.confidence,
            },
        )

    def validate_after(
        self,
        *,
        task_id: str,
        intent: str | None,
        before_snapshot: UIElementStateSnapshot,
        after_element: UIGroundedElement | None,
    ) -> UIStateValidationResult:
        normalized_task_id = _normalize_required(task_id, "task_id")
        normalized_intent = _normalize_optional_text(intent, fallback="click")
        _validate_snapshot(before_snapshot)

        if after_element is None:
            if _is_destructive_intent(normalized_intent):
                return UIStateValidationResult(
                    task_id=normalized_task_id,
                    phase="after",
                    passed=True,
                    reason="critical_after_validation_target_removed_allowed",
                    details={
                        "intent": normalized_intent,
                        "before_element_id": before_snapshot.element_id,
                    },
                )

            return UIStateValidationResult(
                task_id=normalized_task_id,
                phase="after",
                passed=False,
                reason="critical_after_validation_target_missing",
                details={
                    "intent": normalized_intent,
                    "before_element_id": before_snapshot.element_id,
                },
            )

        if after_element.role != before_snapshot.role:
            return UIStateValidationResult(
                task_id=normalized_task_id,
                phase="after",
                passed=False,
                reason="critical_after_validation_role_changed",
                details={
                    "intent": normalized_intent,
                    "before_role": before_snapshot.role,
                    "after_role": after_element.role,
                },
            )

        if after_element.confidence < self.min_after_confidence:
            return UIStateValidationResult(
                task_id=normalized_task_id,
                phase="after",
                passed=False,
                reason="critical_after_validation_low_confidence",
                details={
                    "intent": normalized_intent,
                    "confidence": after_element.confidence,
                    "threshold": self.min_after_confidence,
                },
            )

        iou = _iou(before_snapshot.bbox, after_element.bbox)
        if not _is_destructive_intent(normalized_intent) and iou < self.min_stable_iou:
            return UIStateValidationResult(
                task_id=normalized_task_id,
                phase="after",
                passed=False,
                reason="critical_after_validation_unstable_target",
                details={
                    "intent": normalized_intent,
                    "iou": round(iou, 6),
                    "minimum_iou": self.min_stable_iou,
                },
            )

        if not _is_destructive_intent(normalized_intent) and not _state_flag(after_element.state, "visible", default=True):
            return UIStateValidationResult(
                task_id=normalized_task_id,
                phase="after",
                passed=False,
                reason="critical_after_validation_target_hidden",
                details={"intent": normalized_intent, "element_id": after_element.element_id},
            )

        if normalized_intent == "disable" and _state_flag(after_element.state, "enabled", default=True):
            return UIStateValidationResult(
                task_id=normalized_task_id,
                phase="after",
                passed=False,
                reason="critical_after_validation_disable_intent_not_reflected",
                details={"intent": normalized_intent, "element_id": after_element.element_id},
            )

        return UIStateValidationResult(
            task_id=normalized_task_id,
            phase="after",
            passed=True,
            reason="critical_after_validation_passed",
            details={
                "intent": normalized_intent,
                "element_id": after_element.element_id,
                "iou": round(iou, 6),
                "confidence": after_element.confidence,
            },
        )


def _validate_element(element: UIGroundedElement) -> None:
    if not isinstance(element, UIGroundedElement):
        raise UIStateValidatorError("element must be a UIGroundedElement")
    _normalize_required(element.element_id, "element.element_id")


def _validate_snapshot(snapshot: UIElementStateSnapshot) -> None:
    if not isinstance(snapshot, UIElementStateSnapshot):
        raise UIStateValidatorError("before_snapshot must be a UIElementStateSnapshot")
    _normalize_required(snapshot.snapshot_id, "before_snapshot.snapshot_id")


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


def _is_destructive_intent(intent: str | None) -> bool:
    normalized = _normalize_optional_text(intent, fallback="")
    return normalized in _DESTRUCTIVE_INTENTS


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


def _normalize_required(value: Any, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise UIStateValidatorError(f"{field_name} is required")
    return normalized


def _normalize_optional_text(value: Any, *, fallback: str | None) -> str | None:
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
    "CriticalUIStateValidator",
    "UIElementStateSnapshot",
    "UIStateValidationResult",
    "UIStateValidatorError",
]
