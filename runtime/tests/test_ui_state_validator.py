"""Tests for P8-T6 critical before/after UI state validation."""

from __future__ import annotations

import unittest

from runtime.multimodal import CriticalUIStateValidator, UIGroundedElement


class CriticalUIStateValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = CriticalUIStateValidator(min_after_confidence=0.3, min_stable_iou=0.08)

    def test_before_validation_passes_for_visible_enabled_target(self) -> None:
        element = _element()

        result = self.validator.validate_before(task_id="task-1", intent="open", element=element)

        self.assertTrue(result.passed)
        self.assertEqual(result.phase, "before")

    def test_before_validation_fails_for_missing_target(self) -> None:
        result = self.validator.validate_before(task_id="task-2", intent="open", element=None)

        self.assertFalse(result.passed)
        self.assertIn("missing_target", result.reason)

    def test_after_validation_fails_for_non_destructive_missing_target(self) -> None:
        before = self.validator.capture_before_snapshot(task_id="task-3", scene_id="scene-a", element=_element())

        result = self.validator.validate_after(
            task_id="task-3-post",
            intent="open",
            before_snapshot=before,
            after_element=None,
        )

        self.assertFalse(result.passed)
        self.assertIn("target_missing", result.reason)

    def test_after_validation_allows_missing_target_for_destructive_intent(self) -> None:
        before = self.validator.capture_before_snapshot(task_id="task-4", scene_id="scene-a", element=_element())

        result = self.validator.validate_after(
            task_id="task-4-post",
            intent="delete",
            before_snapshot=before,
            after_element=None,
        )

        self.assertTrue(result.passed)
        self.assertIn("removed_allowed", result.reason)

    def test_after_validation_fails_when_role_changes(self) -> None:
        before = self.validator.capture_before_snapshot(task_id="task-5", scene_id="scene-a", element=_element())
        after = _element(role="link")

        result = self.validator.validate_after(
            task_id="task-5-post",
            intent="open",
            before_snapshot=before,
            after_element=after,
        )

        self.assertFalse(result.passed)
        self.assertIn("role_changed", result.reason)

    def test_after_validation_fails_on_large_non_destructive_bbox_shift(self) -> None:
        before = self.validator.capture_before_snapshot(task_id="task-6", scene_id="scene-a", element=_element())
        after = _element(bbox=(700, 500, 120, 40), normalized_bbox=(0.7, 0.5, 0.12, 0.04))

        result = self.validator.validate_after(
            task_id="task-6-post",
            intent="open",
            before_snapshot=before,
            after_element=after,
        )

        self.assertFalse(result.passed)
        self.assertIn("unstable_target", result.reason)

    def test_after_validation_checks_disable_intent_outcome(self) -> None:
        before = self.validator.capture_before_snapshot(task_id="task-7", scene_id="scene-a", element=_element())
        after_enabled = _element(state={"visible": True, "enabled": True, "selected": False})

        fail_result = self.validator.validate_after(
            task_id="task-7-post-fail",
            intent="disable",
            before_snapshot=before,
            after_element=after_enabled,
        )
        self.assertFalse(fail_result.passed)

        after_disabled = _element(state={"visible": True, "enabled": False, "selected": False})
        pass_result = self.validator.validate_after(
            task_id="task-7-post-pass",
            intent="disable",
            before_snapshot=before,
            after_element=after_disabled,
        )
        self.assertTrue(pass_result.passed)


def _element(
    *,
    element_id: str = "element-1",
    label: str = "Open Settings",
    role: str = "button",
    bbox: tuple[int, int, int, int] = (120, 90, 140, 42),
    normalized_bbox: tuple[float, float, float, float] = (0.12, 0.09, 0.14, 0.042),
    state: dict[str, object] | None = None,
) -> UIGroundedElement:
    return UIGroundedElement(
        element_id=element_id,
        label=label,
        role=role,
        bbox=bbox,
        normalized_bbox=normalized_bbox,
        center=(bbox[0] + (bbox[2] // 2), bbox[1] + (bbox[3] // 2)),
        confidence=0.92,
        source_signals=("detector", "ocr"),
        selector_hints=("#open-settings", "role=button"),
        text_line_ids=("line-1",),
        actionable=True,
        state=dict(state or {"visible": True, "enabled": True, "selected": False}),
    )


if __name__ == "__main__":
    unittest.main()
