"""Visual planner integration with runtime action stages."""

from __future__ import annotations

import json
import string
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from runtime.pipeline.models import PlanResult, PlannedTask, RunContext
from runtime.planner import PlannerInterfaceAdapter

from .ui_grounding import UIGroundedElement, UIStateRepresentation

_RUNTIME_STAGES = ("plan", "execute", "validate", "report")

_DESTRUCTIVE_TOKENS = {
    "delete",
    "remove",
    "wipe",
    "destroy",
    "drop",
    "terminate",
    "revoke",
    "disable",
    "shutdown",
}

_INTENT_ALIASES = {
    "open": "open",
    "click": "click",
    "select": "select",
    "submit": "submit",
    "save": "submit",
    "apply": "submit",
    "confirm": "confirm",
    "approve": "confirm",
    "type": "type",
    "enter": "type",
    "search": "search",
    "toggle": "toggle",
    "switch": "toggle",
    "enable": "enable",
    "disable": "disable",
    "delete": "delete",
    "remove": "delete",
    "close": "close",
}

_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "to",
    "for",
    "of",
    "on",
    "in",
    "with",
    "from",
    "by",
    "then",
    "now",
}


@dataclass(frozen=True)
class VisualStageBinding:
    task_id: str
    runtime_stage: str
    action_stage: str
    element_id: str | None


@dataclass(frozen=True)
class VisualPlanningResult:
    scene_id: str
    plan: PlanResult
    bindings: tuple[VisualStageBinding, ...]
    selected_element_ids: tuple[str, ...]
    action_intent: str
    warnings: tuple[str, ...]


class VisualPlannerError(ValueError):
    """Raised when visual planning receives invalid or incomplete input."""


class VisualActionPlanner:
    """Builds runtime-stage-aware plans from grounded UI state."""

    serializer_version = "visual-runtime-v1"

    def __init__(
        self,
        *,
        min_grounding_confidence: float = 0.5,
        max_actions: int = 3,
        planner_adapter: PlannerInterfaceAdapter | None = None,
    ) -> None:
        if min_grounding_confidence < 0 or min_grounding_confidence > 1:
            raise VisualPlannerError("min_grounding_confidence must be between 0 and 1")
        if max_actions < 1:
            raise VisualPlannerError("max_actions must be at least 1")

        self.min_grounding_confidence = float(min_grounding_confidence)
        self.max_actions = int(max_actions)
        self.planner_adapter = planner_adapter or PlannerInterfaceAdapter()

    def plan(
        self,
        context: RunContext,
        ui_state: UIStateRepresentation,
        *,
        constraints: dict[str, Any] | None = None,
    ) -> VisualPlanningResult:
        _validate_context(context)
        _validate_ui_state(ui_state)

        goal = _normalize_required(context.goal, "context.goal")
        action_intent = _infer_action_intent(goal)
        selected_elements, selection_warnings = self._select_elements(ui_state, goal)

        normalized_constraints = _normalize_constraints(constraints or {})
        base_payload = self.planner_adapter.build_plan_payload(goal, normalized_constraints)

        stage_task_map: dict[str, list[str]] = {stage: [] for stage in _RUNTIME_STAGES}
        bindings: list[VisualStageBinding] = []
        planned_tasks: list[PlannedTask] = []

        scene_bind_id = _stable_task_id("VSN", f"{ui_state.scene_id}:{goal}")
        scene_bind_task = PlannedTask(
            task_id=scene_bind_id,
            description=f"Bind visual scene {ui_state.scene_id} for runtime action planning",
            metadata={
                "depends_on": [],
                "runtime_stage": "plan",
                "action_stage": "scene_bind",
                "scene_id": ui_state.scene_id,
                "requires_confirmation": False,
            },
        )
        _append_task(
            planned_tasks,
            bindings,
            stage_task_map,
            scene_bind_task,
            runtime_stage="plan",
            action_stage="scene_bind",
            element_id=None,
        )

        semantic_last_task_id = scene_bind_id
        for raw_task in base_payload["tasks"]:
            semantic_task_id = f"SEM-{raw_task['task_id']}"
            raw_depends = [f"SEM-{task_id}" for task_id in raw_task.get("depends_on", [])]
            depends_on = raw_depends or [scene_bind_id]
            semantic_task = PlannedTask(
                task_id=semantic_task_id,
                description=f"Semantic step: {raw_task['description']}",
                metadata={
                    "depends_on": depends_on,
                    "runtime_stage": "execute",
                    "action_stage": "semantic_step",
                    "scene_id": ui_state.scene_id,
                    "requires_confirmation": False,
                },
            )
            _append_task(
                planned_tasks,
                bindings,
                stage_task_map,
                semantic_task,
                runtime_stage="execute",
                action_stage="semantic_step",
                element_id=None,
            )
            semantic_last_task_id = semantic_task_id

        for index, element in enumerate(selected_elements, start=1):
            requires_confirmation = self._requires_confirmation(action_intent, element)
            anchor_id = semantic_last_task_id

            precheck_id = _stable_task_id("VPR", f"{element.element_id}:{index}:{goal}")
            precheck_task = PlannedTask(
                task_id=precheck_id,
                description=f"Verify UI readiness for '{element.label}'",
                metadata={
                    "depends_on": [anchor_id],
                    "runtime_stage": "execute",
                    "action_stage": "ui_precheck",
                    "element_id": element.element_id,
                    "element_label": element.label,
                    "element_role": element.role,
                    "selector_hints": list(element.selector_hints),
                    "confidence": element.confidence,
                    "scene_id": ui_state.scene_id,
                    "requires_confirmation": requires_confirmation,
                },
            )
            _append_task(
                planned_tasks,
                bindings,
                stage_task_map,
                precheck_task,
                runtime_stage="execute",
                action_stage="ui_precheck",
                element_id=element.element_id,
            )

            action_id = _stable_task_id("VAC", f"{element.element_id}:{action_intent}:{index}:{goal}")
            action_task = PlannedTask(
                task_id=action_id,
                description=f"{action_intent.title()} '{element.label}' ({element.role})",
                metadata={
                    "depends_on": [precheck_id],
                    "runtime_stage": "execute",
                    "action_stage": "ui_action",
                    "element_id": element.element_id,
                    "element_label": element.label,
                    "element_role": element.role,
                    "selector_hints": list(element.selector_hints),
                    "confidence": element.confidence,
                    "scene_id": ui_state.scene_id,
                    "intent": action_intent,
                    "requires_confirmation": requires_confirmation,
                    "risk_hint": "high" if requires_confirmation else "standard",
                },
            )
            _append_task(
                planned_tasks,
                bindings,
                stage_task_map,
                action_task,
                runtime_stage="execute",
                action_stage="ui_action",
                element_id=element.element_id,
            )

            postcheck_id = _stable_task_id("VPV", f"{element.element_id}:{action_intent}:{index}:{goal}")
            postcheck_task = PlannedTask(
                task_id=postcheck_id,
                description=f"Validate post-action UI state for '{element.label}'",
                metadata={
                    "depends_on": [action_id],
                    "runtime_stage": "validate",
                    "action_stage": "ui_postcheck",
                    "element_id": element.element_id,
                    "element_label": element.label,
                    "element_role": element.role,
                    "selector_hints": list(element.selector_hints),
                    "confidence": element.confidence,
                    "scene_id": ui_state.scene_id,
                    "intent": action_intent,
                    "requires_confirmation": requires_confirmation,
                },
            )
            _append_task(
                planned_tasks,
                bindings,
                stage_task_map,
                postcheck_task,
                runtime_stage="validate",
                action_stage="ui_postcheck",
                element_id=element.element_id,
            )

        warnings = list(ui_state.warnings)
        warnings.extend(selection_warnings)

        payload = {
            "goal": goal,
            "scene_id": ui_state.scene_id,
            "action_intent": action_intent,
            "constraints": normalized_constraints,
            "selected_element_ids": [element.element_id for element in selected_elements],
            "tasks": [_task_to_payload(task) for task in planned_tasks],
            "runtime_stage_task_map": stage_task_map,
            "warnings": warnings,
        }
        serialized_plan = self._serialize(payload)
        plan_id = _stable_plan_id(serialized_plan)

        plan_result = PlanResult(
            plan_id=plan_id,
            tasks=planned_tasks,
            metadata={
                "serializer": self.serializer_version,
                "serialized_plan": serialized_plan,
                "runtime_stage_task_map": stage_task_map,
                "ui_scene_id": ui_state.scene_id,
                "action_intent": action_intent,
                "selected_element_ids": [element.element_id for element in selected_elements],
                "warnings": warnings,
                "base_plan_id": base_payload["plan_id"],
            },
        )

        return VisualPlanningResult(
            scene_id=ui_state.scene_id,
            plan=plan_result,
            bindings=tuple(bindings),
            selected_element_ids=tuple(element.element_id for element in selected_elements),
            action_intent=action_intent,
            warnings=tuple(warnings),
        )

    def _select_elements(
        self,
        ui_state: UIStateRepresentation,
        goal: str,
    ) -> tuple[tuple[UIGroundedElement, ...], tuple[str, ...]]:
        goal_tokens = _tokenize(goal)
        actionable_ids = set(ui_state.actionable_element_ids)

        ranked_actionable: list[tuple[float, UIGroundedElement]] = []
        ranked_visible: list[tuple[float, UIGroundedElement]] = []

        for element in ui_state.elements:
            if not _state_flag(element.state, "visible", default=True):
                continue

            relevance = _goal_relevance(element.label, goal_tokens)
            score = round((element.confidence * 0.68) + (relevance * 0.32), 6)
            if element.element_id in actionable_ids:
                ranked_actionable.append((score, element))
            else:
                ranked_visible.append((score, element))

        ranked_actionable.sort(key=lambda item: (-item[0], item[1].bbox[1], item[1].bbox[0], item[1].element_id))
        ranked_visible.sort(key=lambda item: (-item[0], item[1].bbox[1], item[1].bbox[0], item[1].element_id))

        selected: list[UIGroundedElement] = [
            element
            for _score, element in ranked_actionable
            if element.confidence >= self.min_grounding_confidence
        ][: self.max_actions]

        warnings: list[str] = []
        if not selected and ranked_actionable:
            selected = [ranked_actionable[0][1]]
            warnings.append("Actionable elements were below confidence threshold; using top-ranked fallback with confirmation.")

        if not selected and ranked_visible:
            selected = [ranked_visible[0][1]]
            warnings.append("No actionable UI element was available; using best visible element as fallback target.")

        if not selected:
            warnings.append("No visible UI element was available for visual action planning.")

        return tuple(selected), tuple(warnings)

    def _requires_confirmation(self, action_intent: str, element: UIGroundedElement) -> bool:
        label_tokens = set(_tokenize(element.label))
        is_destructive_intent = action_intent in _DESTRUCTIVE_TOKENS
        has_destructive_label = bool(label_tokens.intersection(_DESTRUCTIVE_TOKENS))
        low_confidence = element.confidence < self.min_grounding_confidence

        return is_destructive_intent or has_destructive_label or low_confidence

    @staticmethod
    def _serialize(payload: dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _append_task(
    task_list: list[PlannedTask],
    bindings: list[VisualStageBinding],
    stage_task_map: dict[str, list[str]],
    task: PlannedTask,
    *,
    runtime_stage: str,
    action_stage: str,
    element_id: str | None,
) -> None:
    task_list.append(task)
    bindings.append(
        VisualStageBinding(
            task_id=task.task_id,
            runtime_stage=runtime_stage,
            action_stage=action_stage,
            element_id=element_id,
        )
    )
    stage_task_map[runtime_stage].append(task.task_id)


def _validate_context(context: RunContext) -> None:
    if not isinstance(context, RunContext):
        raise VisualPlannerError("context must be a RunContext")
    _normalize_required(context.run_id, "context.run_id")
    _normalize_required(context.goal, "context.goal")
    _normalize_required(context.actor_id, "context.actor_id")


def _validate_ui_state(ui_state: UIStateRepresentation) -> None:
    if not isinstance(ui_state, UIStateRepresentation):
        raise VisualPlannerError("ui_state must be a UIStateRepresentation")
    _normalize_required(ui_state.scene_id, "ui_state.scene_id")


def _infer_action_intent(goal: str) -> str:
    for token in _tokenize(goal):
        if token in _INTENT_ALIASES:
            return _INTENT_ALIASES[token]
    return "click"


def _goal_relevance(label: str, goal_tokens: list[str]) -> float:
    if not goal_tokens:
        return 0.0

    label_tokens = set(_tokenize(label))
    if not label_tokens:
        return 0.0

    overlap = len(label_tokens.intersection(goal_tokens))
    if overlap == 0:
        return 0.0

    return round(overlap / float(max(len(goal_tokens), 1)), 6)


def _tokenize(value: str) -> list[str]:
    table = str.maketrans({character: " " for character in string.punctuation})
    normalized = value.translate(table).lower()
    tokens = [token for token in normalized.split() if token and token not in _STOPWORDS]
    return tokens


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


def _normalize_constraints(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VisualPlannerError("constraints must be a dictionary")
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))


def _task_to_payload(task: PlannedTask) -> dict[str, Any]:
    metadata = json.loads(json.dumps(task.metadata, sort_keys=True, separators=(",", ":"), default=str))
    return {
        "task_id": task.task_id,
        "description": task.description,
        "metadata": metadata,
    }


def _stable_task_id(prefix: str, seed: str) -> str:
    digest = sha256(seed.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _stable_plan_id(serialized_plan: str) -> str:
    return sha256(serialized_plan.encode("utf-8")).hexdigest()[:16]


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise VisualPlannerError(f"{field_name} is required")
    return normalized


__all__ = [
    "VisualActionPlanner",
    "VisualPlannerError",
    "VisualPlanningResult",
    "VisualStageBinding",
]
