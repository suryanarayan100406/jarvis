"""Fallback strategy for low-confidence visual planning decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .ui_grounding import UIGroundedElement, UIStateRepresentation

VisualFallbackMode = Literal["proceed", "confirm", "defer"]


@dataclass(frozen=True)
class VisualConfidenceFallbackDecision:
    scene_id: str
    element_id: str
    mode: VisualFallbackMode
    reason: str
    element_confidence: float
    average_confidence: float
    low_confidence_ratio: float
    recommended_actions: tuple[str, ...]


class VisualConfidenceFallbackError(ValueError):
    """Raised when confidence fallback strategy receives invalid input."""


class VisualConfidenceFallbackStrategy:
    """Classifies visual actions into proceed, confirm, or defer fallback modes."""

    def __init__(
        self,
        *,
        min_autonomous_confidence: float = 0.5,
        min_confirmation_confidence: float = 0.35,
        max_low_confidence_ratio: float = 0.65,
    ) -> None:
        if min_autonomous_confidence < 0 or min_autonomous_confidence > 1:
            raise VisualConfidenceFallbackError("min_autonomous_confidence must be between 0 and 1")
        if min_confirmation_confidence < 0 or min_confirmation_confidence > 1:
            raise VisualConfidenceFallbackError("min_confirmation_confidence must be between 0 and 1")
        if max_low_confidence_ratio < 0 or max_low_confidence_ratio > 1:
            raise VisualConfidenceFallbackError("max_low_confidence_ratio must be between 0 and 1")
        if min_confirmation_confidence > min_autonomous_confidence:
            raise VisualConfidenceFallbackError(
                "min_confirmation_confidence cannot exceed min_autonomous_confidence"
            )

        self.min_autonomous_confidence = float(min_autonomous_confidence)
        self.min_confirmation_confidence = float(min_confirmation_confidence)
        self.max_low_confidence_ratio = float(max_low_confidence_ratio)

    def assess(
        self,
        ui_state: UIStateRepresentation,
        element: UIGroundedElement,
    ) -> VisualConfidenceFallbackDecision:
        _validate_ui_state(ui_state)
        _validate_element(element)

        if not ui_state.elements:
            return VisualConfidenceFallbackDecision(
                scene_id=ui_state.scene_id,
                element_id=element.element_id,
                mode="defer",
                reason="scene_empty",
                element_confidence=round(element.confidence, 4),
                average_confidence=0.0,
                low_confidence_ratio=1.0,
                recommended_actions=(
                    "refresh_visual_context",
                    "request_manual_target_selection",
                    "escalate_to_operator",
                ),
            )

        low_confidence_ratio = round(
            len(ui_state.low_confidence_element_ids) / float(len(ui_state.elements)),
            4,
        )
        element_confidence = round(element.confidence, 4)
        average_confidence = round(ui_state.average_confidence, 4)

        if element_confidence < self.min_confirmation_confidence:
            return VisualConfidenceFallbackDecision(
                scene_id=ui_state.scene_id,
                element_id=element.element_id,
                mode="defer",
                reason="element_confidence_critical",
                element_confidence=element_confidence,
                average_confidence=average_confidence,
                low_confidence_ratio=low_confidence_ratio,
                recommended_actions=(
                    "refresh_visual_context",
                    "request_manual_target_selection",
                    "escalate_to_operator",
                ),
            )

        if element_confidence < self.min_autonomous_confidence:
            return VisualConfidenceFallbackDecision(
                scene_id=ui_state.scene_id,
                element_id=element.element_id,
                mode="confirm",
                reason="element_confidence_below_autonomous_threshold",
                element_confidence=element_confidence,
                average_confidence=average_confidence,
                low_confidence_ratio=low_confidence_ratio,
                recommended_actions=(
                    "require_confirmation_checkpoint",
                    "capture_fresh_screenshot_before_action",
                ),
            )

        if low_confidence_ratio >= self.max_low_confidence_ratio:
            return VisualConfidenceFallbackDecision(
                scene_id=ui_state.scene_id,
                element_id=element.element_id,
                mode="confirm",
                reason="scene_low_confidence_ratio_high",
                element_confidence=element_confidence,
                average_confidence=average_confidence,
                low_confidence_ratio=low_confidence_ratio,
                recommended_actions=(
                    "require_confirmation_checkpoint",
                    "request_additional_visual_context",
                ),
            )

        return VisualConfidenceFallbackDecision(
            scene_id=ui_state.scene_id,
            element_id=element.element_id,
            mode="proceed",
            reason="confidence_within_autonomous_limits",
            element_confidence=element_confidence,
            average_confidence=average_confidence,
            low_confidence_ratio=low_confidence_ratio,
            recommended_actions=("continue_with_standard_checks",),
        )

    @staticmethod
    def to_task_metadata(decision: VisualConfidenceFallbackDecision) -> dict[str, object]:
        return {
            "fallback_mode": decision.mode,
            "fallback_reason": decision.reason,
            "fallback_element_confidence": decision.element_confidence,
            "fallback_average_confidence": decision.average_confidence,
            "fallback_low_confidence_ratio": decision.low_confidence_ratio,
            "fallback_recommended_actions": list(decision.recommended_actions),
        }


def _validate_ui_state(ui_state: UIStateRepresentation) -> None:
    if not isinstance(ui_state, UIStateRepresentation):
        raise VisualConfidenceFallbackError("ui_state must be a UIStateRepresentation")


def _validate_element(element: UIGroundedElement) -> None:
    if not isinstance(element, UIGroundedElement):
        raise VisualConfidenceFallbackError("element must be a UIGroundedElement")


__all__ = [
    "VisualFallbackMode",
    "VisualConfidenceFallbackDecision",
    "VisualConfidenceFallbackError",
    "VisualConfidenceFallbackStrategy",
]
