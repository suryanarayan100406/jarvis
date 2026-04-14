"""Safe UI action executor with confirmation checkpoints for risky operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from runtime.pipeline.models import ExecutionResult, PlanResult, PlannedTask, RunContext

from .ui_grounding import UIGroundedElement, UIStateRepresentation


@dataclass(frozen=True)
class UIConfirmationCheckpoint:
    checkpoint_id: str
    token: str
    run_id: str
    task_id: str
    scene_id: str
    element_id: str | None
    intent: str | None
    reason: str
    generated_at: str


@dataclass(frozen=True)
class UIActionExecutionOutcome:
    execution: ExecutionResult
    checkpoints: tuple[UIConfirmationCheckpoint, ...]


class UIActionExecutorError(ValueError):
    """Raised when UI action execution contracts are invalid or unsafe."""


class SafeUIActionExecutor:
    """Executes visual runtime action-stage tasks with safety checkpoints."""

    actionable_stages = {"ui_precheck", "ui_action", "ui_postcheck"}
    passthrough_stages = {"scene_bind", "semantic_step"}

    def __init__(self, *, min_precheck_confidence: float = 0.5, stop_on_blocked: bool = True) -> None:
        if min_precheck_confidence < 0 or min_precheck_confidence > 1:
            raise UIActionExecutorError("min_precheck_confidence must be between 0 and 1")

        self.min_precheck_confidence = float(min_precheck_confidence)
        self.stop_on_blocked = bool(stop_on_blocked)
        self._active_checkpoints: dict[str, UIConfirmationCheckpoint] = {}

    def execute(
        self,
        context: RunContext,
        plan: PlanResult,
        ui_state: UIStateRepresentation,
        *,
        confirmation_tokens: dict[str, str] | None = None,
    ) -> UIActionExecutionOutcome:
        _validate_context(context)
        _validate_plan(plan)
        _validate_ui_state(ui_state)
        self._validate_scene_binding(plan, ui_state)

        tokens = dict(confirmation_tokens or {})
        element_index = {element.element_id: element for element in ui_state.elements}
        completed_action_elements: set[str] = set()
        outputs: list[dict[str, Any]] = []
        issued_checkpoints: list[UIConfirmationCheckpoint] = []

        runtime_stage_counts: dict[str, int] = {}
        final_status = "success"

        for task in plan.tasks:
            metadata = _as_dict(task.metadata)
            runtime_stage = _normalize_optional_text(metadata.get("runtime_stage"), fallback="execute")
            action_stage = _normalize_optional_text(metadata.get("action_stage"), fallback="generic")
            runtime_stage_counts[runtime_stage] = runtime_stage_counts.get(runtime_stage, 0) + 1

            base_output = {
                "task_id": task.task_id,
                "runtime_stage": runtime_stage,
                "action_stage": action_stage,
                "status": "success",
            }

            if action_stage in self.passthrough_stages:
                base_output["result"] = "pass_through"
                outputs.append(base_output)
                continue

            if action_stage not in self.actionable_stages:
                base_output["result"] = "no_op"
                outputs.append(base_output)
                continue

            element_id = _normalize_optional_text(metadata.get("element_id"), fallback=None)
            element = element_index.get(element_id) if element_id is not None else None

            if action_stage == "ui_precheck":
                precheck = self._run_precheck(task, metadata, element)
                outputs.append({**base_output, **precheck})
                if precheck["status"] != "success":
                    final_status = "blocked"
                    if self.stop_on_blocked:
                        break
                continue

            if action_stage == "ui_action":
                requires_confirmation = _as_bool(metadata.get("requires_confirmation", False))
                checkpoint_reason = self._checkpoint_reason(metadata, element)
                if requires_confirmation:
                    provided_token = tokens.get(task.task_id)
                    if provided_token is None:
                        checkpoint = self.issue_confirmation_checkpoint(
                            context,
                            task,
                            ui_state=ui_state,
                            reason=checkpoint_reason,
                        )
                        issued_checkpoints.append(checkpoint)
                        outputs.append(
                            {
                                **base_output,
                                "status": "awaiting_confirmation",
                                "checkpoint_token": checkpoint.token,
                                "message": "Confirmation checkpoint required before UI action execution.",
                            }
                        )
                        final_status = "awaiting_confirmation"
                        if self.stop_on_blocked:
                            break
                        continue

                    try:
                        self.consume_confirmation_checkpoint(
                            context,
                            task,
                            ui_state=ui_state,
                            token=provided_token,
                            reason=checkpoint_reason,
                        )
                    except UIActionExecutorError as exc:
                        outputs.append(
                            {
                                **base_output,
                                "status": "blocked",
                                "error": str(exc),
                            }
                        )
                        final_status = "blocked"
                        if self.stop_on_blocked:
                            break
                        continue

                action_validation = self._validate_action_target(metadata, element)
                if action_validation["status"] != "success":
                    outputs.append({**base_output, **action_validation})
                    final_status = "blocked"
                    if self.stop_on_blocked:
                        break
                    continue

                outputs.append({**base_output, **action_validation, "result": "ui_action_executed"})
                if element_id is not None:
                    completed_action_elements.add(element_id)
                continue

            if action_stage == "ui_postcheck":
                postcheck = self._run_postcheck(metadata, element, completed_action_elements)
                outputs.append({**base_output, **postcheck})
                if postcheck["status"] != "success":
                    final_status = "blocked"
                    if self.stop_on_blocked:
                        break
                continue

        if final_status == "success" and any(item["status"] == "awaiting_confirmation" for item in outputs):
            final_status = "awaiting_confirmation"
        if final_status == "success" and any(item["status"] == "blocked" for item in outputs):
            final_status = "blocked"

        execution = ExecutionResult(
            status=final_status,
            outputs=outputs,
            metrics={
                "executed_task_count": len(outputs),
                "checkpoint_count": len(issued_checkpoints),
                "runtime_stage_counts": runtime_stage_counts,
            },
        )
        return UIActionExecutionOutcome(execution=execution, checkpoints=tuple(issued_checkpoints))

    def issue_confirmation_checkpoint(
        self,
        context: RunContext,
        task: PlannedTask,
        *,
        ui_state: UIStateRepresentation,
        reason: str,
    ) -> UIConfirmationCheckpoint:
        _validate_context(context)
        _validate_ui_state(ui_state)
        _validate_task(task)
        normalized_reason = _normalize_required(reason, "reason")

        token = self._build_confirmation_token(context, task, ui_state=ui_state, reason=normalized_reason)
        metadata = _as_dict(task.metadata)
        checkpoint = UIConfirmationCheckpoint(
            checkpoint_id=_hash_text(f"{token}:{task.task_id}"),
            token=token,
            run_id=context.run_id,
            task_id=task.task_id,
            scene_id=ui_state.scene_id,
            element_id=_normalize_optional_text(metadata.get("element_id"), fallback=None),
            intent=_normalize_optional_text(metadata.get("intent"), fallback=None),
            reason=normalized_reason,
            generated_at=_utc_now_iso(),
        )
        self._active_checkpoints[token] = checkpoint
        return checkpoint

    def consume_confirmation_checkpoint(
        self,
        context: RunContext,
        task: PlannedTask,
        *,
        ui_state: UIStateRepresentation,
        token: str,
        reason: str,
    ) -> UIConfirmationCheckpoint:
        _validate_context(context)
        _validate_ui_state(ui_state)
        _validate_task(task)

        normalized_token = _normalize_required(token, "token")
        normalized_reason = _normalize_required(reason, "reason")
        expected = self._build_confirmation_token(context, task, ui_state=ui_state, reason=normalized_reason)
        if normalized_token != expected:
            raise UIActionExecutorError("Confirmation token does not match the expected UI action checkpoint payload")

        checkpoint = self._active_checkpoints.get(normalized_token)
        if checkpoint is None:
            raise UIActionExecutorError("Unknown or expired confirmation checkpoint token")

        self._active_checkpoints.pop(normalized_token, None)
        return checkpoint

    def _run_precheck(
        self,
        task: PlannedTask,
        metadata: dict[str, Any],
        element: UIGroundedElement | None,
    ) -> dict[str, Any]:
        if element is None:
            return {
                "status": "blocked",
                "error": "UI precheck failed: element was not found in current UI state.",
            }

        if not _state_flag(element.state, "visible", default=True):
            return {
                "status": "blocked",
                "error": "UI precheck failed: element is not visible.",
            }

        if not _state_flag(element.state, "enabled", default=True):
            return {
                "status": "blocked",
                "error": "UI precheck failed: element is disabled.",
            }

        requires_confirmation = _as_bool(metadata.get("requires_confirmation", False))
        if element.confidence < self.min_precheck_confidence and not requires_confirmation:
            return {
                "status": "blocked",
                "error": "UI precheck failed: grounding confidence is below minimum threshold.",
                "confidence": element.confidence,
            }

        return {
            "status": "success",
            "element_id": element.element_id,
            "confidence": element.confidence,
            "selector_hints": list(element.selector_hints),
        }

    def _validate_action_target(
        self,
        metadata: dict[str, Any],
        element: UIGroundedElement | None,
    ) -> dict[str, Any]:
        if element is None:
            return {
                "status": "blocked",
                "error": "UI action failed: target element was not found.",
            }

        if not _state_flag(element.state, "visible", default=True):
            return {
                "status": "blocked",
                "error": "UI action failed: target element is not visible.",
            }

        if not _state_flag(element.state, "enabled", default=True):
            return {
                "status": "blocked",
                "error": "UI action failed: target element is disabled.",
            }

        return {
            "status": "success",
            "element_id": element.element_id,
            "element_role": element.role,
            "element_label": element.label,
            "intent": _normalize_optional_text(metadata.get("intent"), fallback="click"),
            "selector_hints": list(element.selector_hints),
        }

    def _run_postcheck(
        self,
        metadata: dict[str, Any],
        element: UIGroundedElement | None,
        completed_action_elements: set[str],
    ) -> dict[str, Any]:
        element_id = _normalize_optional_text(metadata.get("element_id"), fallback=None)
        if element_id is None:
            return {
                "status": "blocked",
                "error": "UI postcheck failed: missing element_id metadata.",
            }

        if element is None:
            return {
                "status": "blocked",
                "error": "UI postcheck failed: target element no longer exists.",
            }

        if element_id not in completed_action_elements:
            return {
                "status": "blocked",
                "error": "UI postcheck failed: matching ui_action did not complete for target element.",
            }

        return {
            "status": "success",
            "element_id": element.element_id,
            "confirmed": True,
        }

    def _checkpoint_reason(self, metadata: dict[str, Any], element: UIGroundedElement | None) -> str:
        if _as_bool(metadata.get("requires_confirmation", False)):
            risk_hint = _normalize_optional_text(metadata.get("risk_hint"), fallback="elevated")
            intent = _normalize_optional_text(metadata.get("intent"), fallback="click")
            label = element.label if element is not None else _normalize_optional_text(metadata.get("element_label"), fallback="target")
            return f"{risk_hint}_risk_{intent}_{label}"
        return "standard_ui_action"

    def _build_confirmation_token(
        self,
        context: RunContext,
        task: PlannedTask,
        *,
        ui_state: UIStateRepresentation,
        reason: str,
    ) -> str:
        metadata = _as_dict(task.metadata)
        canonical_payload = json.dumps(
            {
                "run_id": context.run_id,
                "task_id": task.task_id,
                "scene_id": ui_state.scene_id,
                "element_id": _normalize_optional_text(metadata.get("element_id"), fallback=""),
                "intent": _normalize_optional_text(metadata.get("intent"), fallback=""),
                "risk_hint": _normalize_optional_text(metadata.get("risk_hint"), fallback=""),
                "reason": _normalize_required(reason, "reason"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"uicp-{_hash_text(canonical_payload)[:24]}"

    @staticmethod
    def _validate_scene_binding(plan: PlanResult, ui_state: UIStateRepresentation) -> None:
        metadata = _as_dict(plan.metadata)
        scene_id = _normalize_optional_text(metadata.get("ui_scene_id"), fallback=None)
        if scene_id is not None and scene_id != ui_state.scene_id:
            raise UIActionExecutorError("plan.ui_scene_id does not match the provided UI state scene_id")


def _validate_context(context: RunContext) -> None:
    if not isinstance(context, RunContext):
        raise UIActionExecutorError("context must be a RunContext")
    _normalize_required(context.run_id, "context.run_id")
    _normalize_required(context.goal, "context.goal")
    _normalize_required(context.actor_id, "context.actor_id")


def _validate_plan(plan: PlanResult) -> None:
    if not isinstance(plan, PlanResult):
        raise UIActionExecutorError("plan must be a PlanResult")
    _normalize_required(plan.plan_id, "plan.plan_id")


def _validate_task(task: PlannedTask) -> None:
    if not isinstance(task, PlannedTask):
        raise UIActionExecutorError("task must be a PlannedTask")
    _normalize_required(task.task_id, "task.task_id")


def _validate_ui_state(ui_state: UIStateRepresentation) -> None:
    if not isinstance(ui_state, UIStateRepresentation):
        raise UIActionExecutorError("ui_state must be a UIStateRepresentation")
    _normalize_required(ui_state.scene_id, "ui_state.scene_id")


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return bool(value)


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


def _normalize_required(value: Any, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise UIActionExecutorError(f"{field_name} is required")
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
    "SafeUIActionExecutor",
    "UIActionExecutionOutcome",
    "UIActionExecutorError",
    "UIConfirmationCheckpoint",
]
