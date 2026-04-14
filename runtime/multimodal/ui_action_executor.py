"""Safe UI action executor with confirmation checkpoints for risky operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from runtime.pipeline.models import ExecutionResult, PlanResult, PlannedTask, RunContext

from .ui_grounding import UIGroundedElement, UIStateRepresentation
from .ui_state_validator import (
    CriticalUIStateValidator,
    UIElementStateSnapshot,
    UIStateValidationResult,
)


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

    def __init__(
        self,
        *,
        min_precheck_confidence: float = 0.5,
        stop_on_blocked: bool = True,
        state_validator: CriticalUIStateValidator | None = None,
    ) -> None:
        if min_precheck_confidence < 0 or min_precheck_confidence > 1:
            raise UIActionExecutorError("min_precheck_confidence must be between 0 and 1")

        self.min_precheck_confidence = float(min_precheck_confidence)
        self.stop_on_blocked = bool(stop_on_blocked)
        self.state_validator = state_validator or CriticalUIStateValidator()
        self._active_checkpoints: dict[str, UIConfirmationCheckpoint] = {}

    def execute(
        self,
        context: RunContext,
        plan: PlanResult,
        ui_state: UIStateRepresentation,
        *,
        post_action_ui_state: UIStateRepresentation | None = None,
        confirmation_tokens: dict[str, str] | None = None,
    ) -> UIActionExecutionOutcome:
        _validate_context(context)
        _validate_plan(plan)
        _validate_ui_state(ui_state)
        effective_after_state = post_action_ui_state or ui_state
        _validate_ui_state(effective_after_state)
        self._validate_scene_binding(plan, ui_state)

        tokens = dict(confirmation_tokens or {})
        before_element_index = {element.element_id: element for element in ui_state.elements}
        after_element_index = {element.element_id: element for element in effective_after_state.elements}
        completed_action_elements: set[str] = set()
        critical_action_ids: set[str] = set()
        critical_before_snapshots: dict[str, UIElementStateSnapshot] = {}
        validation_records: list[dict[str, Any]] = []
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
            before_element = before_element_index.get(element_id) if element_id is not None else None

            if action_stage == "ui_precheck":
                precheck = self._run_precheck(task, metadata, before_element)
                outputs.append({**base_output, **precheck})
                if precheck["status"] != "success":
                    final_status = "blocked"
                    if self.stop_on_blocked:
                        break
                continue

            if action_stage == "ui_action":
                fallback_mode = _fallback_mode(metadata)
                if fallback_mode == "defer":
                    outputs.append(
                        {
                            **base_output,
                            "status": "blocked",
                            "error": "UI action deferred: visual confidence fallback requires manual operator review.",
                            "fallback_mode": fallback_mode,
                            "fallback_reason": _normalize_optional_text(
                                metadata.get("fallback_reason"),
                                fallback="visual_confidence_low",
                            ),
                            "recommended_actions": _normalize_recommended_actions(
                                metadata.get("fallback_recommended_actions")
                            ),
                        }
                    )
                    final_status = "blocked"
                    if self.stop_on_blocked:
                        break
                    continue

                critical_action = self._is_critical_action(metadata)
                intent = _normalize_optional_text(metadata.get("intent"), fallback="click")
                if critical_action:
                    before_validation = self.state_validator.validate_before(
                        task_id=task.task_id,
                        intent=intent,
                        element=before_element,
                    )
                    validation_records.append(_validation_to_record(before_validation))
                    if not before_validation.passed:
                        outputs.append(
                            {
                                **base_output,
                                "status": "blocked",
                                "error": before_validation.reason,
                                "validation_phase": "before",
                                "validation_details": dict(before_validation.details),
                            }
                        )
                        final_status = "blocked"
                        if self.stop_on_blocked:
                            break
                        continue

                    if before_element is None:
                        outputs.append(
                            {
                                **base_output,
                                "status": "blocked",
                                "error": "Critical before-state snapshot could not be captured for missing target.",
                            }
                        )
                        final_status = "blocked"
                        if self.stop_on_blocked:
                            break
                        continue

                    before_snapshot = self.state_validator.capture_before_snapshot(
                        task_id=task.task_id,
                        scene_id=ui_state.scene_id,
                        element=before_element,
                    )
                    critical_action_ids.add(task.task_id)
                    critical_before_snapshots[task.task_id] = before_snapshot

                requires_confirmation = _as_bool(metadata.get("requires_confirmation", False))
                checkpoint_reason = self._checkpoint_reason(metadata, before_element)
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

                action_validation = self._validate_action_target(metadata, before_element)
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
                after_element = after_element_index.get(element_id) if element_id is not None else None
                dependency_task_id = _resolve_dependency_task_id(metadata.get("depends_on"))
                critical_dependency = (
                    dependency_task_id in critical_action_ids
                    or self._is_critical_action(metadata)
                )
                if critical_dependency:
                    snapshot_key = dependency_task_id if dependency_task_id in critical_before_snapshots else task.task_id
                    before_snapshot = critical_before_snapshots.get(snapshot_key)
                    if before_snapshot is None:
                        outputs.append(
                            {
                                **base_output,
                                "status": "blocked",
                                "error": "Critical after-state validation missing matching before-state snapshot.",
                                "validation_phase": "after",
                            }
                        )
                        final_status = "blocked"
                        if self.stop_on_blocked:
                            break
                        continue

                    after_validation = self.state_validator.validate_after(
                        task_id=task.task_id,
                        intent=_normalize_optional_text(metadata.get("intent"), fallback="click"),
                        before_snapshot=before_snapshot,
                        after_element=after_element,
                    )
                    validation_records.append(_validation_to_record(after_validation))
                    if not after_validation.passed:
                        outputs.append(
                            {
                                **base_output,
                                "status": "blocked",
                                "error": after_validation.reason,
                                "validation_phase": "after",
                                "validation_details": dict(after_validation.details),
                            }
                        )
                        final_status = "blocked"
                        if self.stop_on_blocked:
                            break
                        continue

                postcheck = self._run_postcheck(
                    metadata,
                    after_element,
                    completed_action_elements,
                    intent=_normalize_optional_text(metadata.get("intent"), fallback="click"),
                )
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

        validation_passed_count = sum(1 for record in validation_records if record.get("passed") is True)
        validation_failed_count = sum(1 for record in validation_records if record.get("passed") is False)

        artifacts: list[dict[str, Any]] = []
        if validation_records:
            artifacts.append(
                {
                    "artifact_type": "ui_state_validation",
                    "record_count": len(validation_records),
                    "records": validation_records,
                }
            )

        execution = ExecutionResult(
            status=final_status,
            outputs=outputs,
            artifacts=artifacts,
            metrics={
                "executed_task_count": len(outputs),
                "checkpoint_count": len(issued_checkpoints),
                "runtime_stage_counts": runtime_stage_counts,
                "critical_state_validation_count": len(validation_records),
                "critical_state_validation_passed": validation_passed_count,
                "critical_state_validation_failed": validation_failed_count,
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

        fallback_mode = _fallback_mode(metadata)
        fallback_reason = _normalize_optional_text(metadata.get("fallback_reason"), fallback="visual_confidence_low")
        fallback_actions = _normalize_recommended_actions(metadata.get("fallback_recommended_actions"))

        if fallback_mode == "defer":
            return {
                "status": "blocked",
                "error": "UI precheck fallback: visual confidence is critically low for autonomous actioning.",
                "fallback_mode": fallback_mode,
                "fallback_reason": fallback_reason,
                "recommended_actions": fallback_actions,
                "confidence": element.confidence,
            }

        requires_confirmation = _as_bool(metadata.get("requires_confirmation", False))
        if element.confidence < self.min_precheck_confidence and not requires_confirmation:
            return {
                "status": "blocked",
                "error": "UI precheck failed: grounding confidence is below minimum threshold.",
                "confidence": element.confidence,
            }

        result = {
            "status": "success",
            "element_id": element.element_id,
            "confidence": element.confidence,
            "selector_hints": list(element.selector_hints),
        }
        if fallback_mode == "confirm":
            result["fallback_mode"] = fallback_mode
            result["fallback_reason"] = fallback_reason
            result["recommended_actions"] = fallback_actions

        return result

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
        *,
        intent: str | None,
    ) -> dict[str, Any]:
        element_id = _normalize_optional_text(metadata.get("element_id"), fallback=None)
        if element_id is None:
            return {
                "status": "blocked",
                "error": "UI postcheck failed: missing element_id metadata.",
            }

        if element_id not in completed_action_elements:
            return {
                "status": "blocked",
                "error": "UI postcheck failed: matching ui_action did not complete for target element.",
            }

        if element is None:
            if _is_destructive_intent(intent):
                return {
                    "status": "success",
                    "element_id": element_id,
                    "confirmed": True,
                    "note": "Target element missing after destructive action.",
                }
            return {
                "status": "blocked",
                "error": "UI postcheck failed: target element no longer exists.",
            }

        return {
            "status": "success",
            "element_id": element.element_id,
            "confirmed": True,
        }

    @staticmethod
    def _is_critical_action(metadata: dict[str, Any]) -> bool:
        requires_confirmation = _as_bool(metadata.get("requires_confirmation", False))
        risk_hint = _normalize_optional_text(metadata.get("risk_hint"), fallback="standard")
        return requires_confirmation or risk_hint in {"high", "critical"}

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


def _fallback_mode(metadata: dict[str, Any]) -> str:
    mode = _normalize_optional_text(metadata.get("fallback_mode"), fallback="proceed")
    if mode not in {"proceed", "confirm", "defer"}:
        return "proceed"
    return mode


def _normalize_recommended_actions(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        action = _normalize_optional_text(item, fallback=None)
        if action is None or action in seen:
            continue
        seen.add(action)
        normalized.append(action)
    return normalized


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
    return normalized in {"delete", "remove", "wipe", "drop", "destroy", "disable"}


def _resolve_dependency_task_id(depends_on_value: Any) -> str | None:
    if not isinstance(depends_on_value, (list, tuple)):
        return None

    for dependency in depends_on_value:
        normalized = " ".join(str(dependency).split())
        if normalized:
            return normalized
    return None


def _validation_to_record(result: UIStateValidationResult) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "phase": result.phase,
        "passed": result.passed,
        "reason": result.reason,
        "details": dict(result.details),
    }


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
