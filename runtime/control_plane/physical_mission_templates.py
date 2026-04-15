"""Mission planner templates for approved physical task workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from string import Formatter
from typing import Any, Literal
from uuid import uuid4

from .physical_device_registry import PhysicalDeviceRegistry

ExecutionMode = Literal["simulation", "live"]

_RISK_RANK: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

_RESERVED_BINDINGS = {"mission_id", "template_id", "execution_mode"}


@dataclass(frozen=True)
class PhysicalMissionTemplateStep:
    step_id: str
    device_binding: str
    capability_id: str
    payload_template: dict[str, Any]
    payload_required_bindings: tuple[str, ...]
    description: str | None


@dataclass(frozen=True)
class PhysicalMissionTemplate:
    template_id: str
    name: str
    description: str | None
    execution_modes: tuple[ExecutionMode, ...]
    approved: bool
    required_bindings: tuple[str, ...]
    steps: tuple[PhysicalMissionTemplateStep, ...]
    tags: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PhysicalMissionPlanStep:
    sequence: int
    step_id: str
    device_id: str
    connector_id: str
    capability_id: str
    payload: dict[str, Any]
    risk_tier: str
    requires_sandbox_approval: bool


@dataclass(frozen=True)
class PhysicalMissionExecutionPlan:
    plan_id: str
    mission_id: str
    template_id: str
    execution_mode: ExecutionMode
    created_at: str
    requires_sandbox_approval: bool
    max_risk_tier: str
    required_controls: tuple[str, ...]
    bindings: dict[str, str]
    steps: tuple[PhysicalMissionPlanStep, ...]


class PhysicalMissionTemplateError(ValueError):
    """Raised when mission template registration or rendering fails."""


class PhysicalMissionTemplatePlanner:
    """Registers approved mission templates and renders executable physical plans."""

    def __init__(self, device_registry: PhysicalDeviceRegistry) -> None:
        self.device_registry = device_registry
        self._templates: dict[str, PhysicalMissionTemplate] = {}

    def register_template(
        self,
        *,
        template_id: str,
        name: str,
        steps: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        execution_modes: list[ExecutionMode] | tuple[ExecutionMode, ...] | None = None,
        approved: bool = True,
        description: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PhysicalMissionTemplate:
        normalized_template_id = _normalize_required(template_id, "template_id").lower()
        if normalized_template_id in self._templates:
            raise PhysicalMissionTemplateError(
                f"Mission template already exists: {normalized_template_id}"
            )

        normalized_name = _normalize_required(name, "name")
        normalized_modes = _normalize_execution_modes(execution_modes)
        normalized_steps = _normalize_template_steps(steps)
        required_bindings = _collect_required_bindings(normalized_steps)

        template = PhysicalMissionTemplate(
            template_id=normalized_template_id,
            name=normalized_name,
            description=_normalize_optional(description),
            execution_modes=normalized_modes,
            approved=bool(approved),
            required_bindings=required_bindings,
            steps=normalized_steps,
            tags=_normalize_tags(tags or ()),
            metadata=dict(metadata or {}),
        )
        self._templates[normalized_template_id] = template
        return template

    def set_template_approval(self, template_id: str, *, approved: bool) -> PhysicalMissionTemplate:
        template = self.get_template(template_id)
        updated = PhysicalMissionTemplate(
            template_id=template.template_id,
            name=template.name,
            description=template.description,
            execution_modes=template.execution_modes,
            approved=bool(approved),
            required_bindings=template.required_bindings,
            steps=template.steps,
            tags=template.tags,
            metadata=dict(template.metadata),
        )
        self._templates[template.template_id] = updated
        return updated

    def get_template(self, template_id: str) -> PhysicalMissionTemplate:
        normalized_template_id = _normalize_required(template_id, "template_id").lower()
        template = self._templates.get(normalized_template_id)
        if template is None:
            raise KeyError(f"Unknown mission template: {normalized_template_id}")
        return template

    def list_templates(
        self,
        *,
        approved_only: bool = False,
        execution_mode: ExecutionMode | str | None = None,
    ) -> list[PhysicalMissionTemplate]:
        normalized_mode = (
            _normalize_execution_mode(execution_mode)
            if execution_mode is not None
            else None
        )

        templates = list(self._templates.values())
        if approved_only:
            templates = [template for template in templates if template.approved]
        if normalized_mode is not None:
            templates = [
                template
                for template in templates
                if normalized_mode in template.execution_modes
            ]

        templates.sort(key=lambda item: item.template_id)
        return templates

    def render_plan(
        self,
        template_id: str,
        *,
        mission_id: str,
        bindings: dict[str, Any],
        execution_mode: ExecutionMode | str = "simulation",
    ) -> PhysicalMissionExecutionPlan:
        template = self.get_template(template_id)
        if not template.approved:
            raise PhysicalMissionTemplateError(
                f"Mission template {template.template_id} is not approved"
            )

        normalized_mission_id = _normalize_required(mission_id, "mission_id")
        normalized_mode = _normalize_execution_mode(execution_mode)
        if normalized_mode not in template.execution_modes:
            allowed = ", ".join(template.execution_modes)
            raise PhysicalMissionTemplateError(
                f"Execution mode {normalized_mode} is not allowed for template {template.template_id}. "
                f"Allowed: {allowed}"
            )

        normalized_bindings = _normalize_bindings(bindings)
        missing_bindings = [
            binding
            for binding in template.required_bindings
            if binding not in normalized_bindings
        ]
        if missing_bindings:
            raise PhysicalMissionTemplateError(
                f"Template {template.template_id} is missing required bindings: "
                f"{', '.join(missing_bindings)}"
            )

        render_context: dict[str, str] = {
            "mission_id": normalized_mission_id,
            "template_id": template.template_id,
            "execution_mode": normalized_mode,
            **normalized_bindings,
        }

        plan_steps: list[PhysicalMissionPlanStep] = []
        requires_sandbox_approval = False
        max_risk_tier = "low"

        for sequence, step in enumerate(template.steps, start=1):
            device_binding_value = normalized_bindings[step.device_binding]
            device_id = _normalize_required(device_binding_value, "device_binding_value").lower()

            try:
                device = self.device_registry.get_device(device_id)
            except KeyError as exc:
                raise PhysicalMissionTemplateError(
                    f"Unknown device binding for step {step.step_id}: {device_id}"
                ) from exc

            if not device.enabled:
                raise PhysicalMissionTemplateError(
                    f"Device {device.device_id} is disabled and cannot be planned"
                )

            try:
                capability = self.device_registry.get_capability_profile(
                    device.device_id,
                    step.capability_id,
                )
            except Exception as exc:
                raise PhysicalMissionTemplateError(
                    f"Template step {step.step_id} references unsupported capability "
                    f"{step.capability_id} for device {device.device_id}"
                ) from exc

            payload = _render_payload_template(
                step.payload_template,
                context=render_context,
                template_id=template.template_id,
                step_id=step.step_id,
            )

            plan_steps.append(
                PhysicalMissionPlanStep(
                    sequence=sequence,
                    step_id=step.step_id,
                    device_id=device.device_id,
                    connector_id=device.connector_id,
                    capability_id=capability.capability_id,
                    payload=payload,
                    risk_tier=capability.risk_tier,
                    requires_sandbox_approval=capability.requires_sandbox_approval,
                )
            )

            requires_sandbox_approval = (
                requires_sandbox_approval or capability.requires_sandbox_approval
            )
            if _RISK_RANK[capability.risk_tier] > _RISK_RANK[max_risk_tier]:
                max_risk_tier = capability.risk_tier

        required_controls = _derive_required_controls(
            execution_mode=normalized_mode,
            requires_sandbox_approval=requires_sandbox_approval,
            max_risk_tier=max_risk_tier,
        )

        return PhysicalMissionExecutionPlan(
            plan_id=str(uuid4()),
            mission_id=normalized_mission_id,
            template_id=template.template_id,
            execution_mode=normalized_mode,
            created_at=_utc_now_iso(),
            requires_sandbox_approval=requires_sandbox_approval,
            max_risk_tier=max_risk_tier,
            required_controls=required_controls,
            bindings={
                key: normalized_bindings[key]
                for key in sorted(normalized_bindings)
            },
            steps=tuple(plan_steps),
        )


def _normalize_template_steps(
    steps: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> tuple[PhysicalMissionTemplateStep, ...]:
    normalized: list[PhysicalMissionTemplateStep] = []
    seen_step_ids: set[str] = set()

    for step in steps:
        if not isinstance(step, dict):
            raise PhysicalMissionTemplateError("steps must contain dictionary entries")

        step_id = _normalize_required(str(step.get("step_id", "")), "step_id").lower()
        if step_id in seen_step_ids:
            raise PhysicalMissionTemplateError(f"Duplicate step_id: {step_id}")
        seen_step_ids.add(step_id)

        device_binding = _normalize_required(
            str(step.get("device_binding", "")),
            "device_binding",
        )
        capability_id = _normalize_required(
            str(step.get("capability_id", "")),
            "capability_id",
        ).lower()

        payload_template = step.get("payload_template", {})
        if not isinstance(payload_template, dict):
            raise PhysicalMissionTemplateError(
                f"payload_template for step {step_id} must be a dictionary"
            )

        payload_required_bindings = _extract_payload_bindings(payload_template)
        normalized.append(
            PhysicalMissionTemplateStep(
                step_id=step_id,
                device_binding=device_binding,
                capability_id=capability_id,
                payload_template=dict(payload_template),
                payload_required_bindings=payload_required_bindings,
                description=_normalize_optional(step.get("description")),
            )
        )

    if not normalized:
        raise PhysicalMissionTemplateError("steps must include at least one template step")

    return tuple(normalized)


def _collect_required_bindings(
    steps: tuple[PhysicalMissionTemplateStep, ...],
) -> tuple[str, ...]:
    required: set[str] = set()
    for step in steps:
        required.add(step.device_binding)
        for binding in step.payload_required_bindings:
            if binding in _RESERVED_BINDINGS:
                continue
            required.add(binding)
    return tuple(sorted(required))


def _extract_payload_bindings(payload_template: dict[str, Any]) -> tuple[str, ...]:
    bindings: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                _walk(nested)
            return
        if isinstance(value, list):
            for nested in value:
                _walk(nested)
            return
        if isinstance(value, tuple):
            for nested in value:
                _walk(nested)
            return
        if isinstance(value, str):
            for _, field_name, _, _ in Formatter().parse(value):
                if field_name is None:
                    continue
                root = field_name.split(".", 1)[0].split("[", 1)[0]
                bindings.add(_normalize_required(root, "payload_binding"))

    _walk(payload_template)
    return tuple(sorted(bindings))


def _render_payload_template(
    payload_template: dict[str, Any],
    *,
    context: dict[str, str],
    template_id: str,
    step_id: str,
) -> dict[str, Any]:
    def _render_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): _render_value(nested) for key, nested in value.items()}
        if isinstance(value, list):
            return [_render_value(nested) for nested in value]
        if isinstance(value, tuple):
            return [_render_value(nested) for nested in value]
        if isinstance(value, str):
            try:
                rendered = value.format(**context)
            except Exception as exc:
                raise PhysicalMissionTemplateError(
                    f"Failed to render payload for template {template_id} step {step_id}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            return rendered
        return value

    return _render_value(payload_template)


def _derive_required_controls(
    *,
    execution_mode: ExecutionMode,
    requires_sandbox_approval: bool,
    max_risk_tier: str,
) -> tuple[str, ...]:
    controls: set[str] = set()
    if execution_mode == "live":
        controls.add("simulation_approval_required")
        if requires_sandbox_approval:
            controls.add("sandbox_approval_required")
        if max_risk_tier == "high":
            controls.add("supervisor_ack_required")
        if max_risk_tier == "critical":
            controls.add("human_approval_required")

    return tuple(sorted(controls))


def _normalize_bindings(bindings: dict[str, Any]) -> dict[str, str]:
    if not isinstance(bindings, dict):
        raise PhysicalMissionTemplateError("bindings must be a dictionary")

    normalized: dict[str, str] = {}
    for key, value in bindings.items():
        normalized_key = _normalize_required(str(key), "binding_key")
        normalized_value = _normalize_required(str(value), f"binding_value:{normalized_key}")
        normalized[normalized_key] = normalized_value
    return normalized


def _normalize_execution_modes(
    execution_modes: list[ExecutionMode] | tuple[ExecutionMode, ...] | None,
) -> tuple[ExecutionMode, ...]:
    if execution_modes is None:
        return ("live", "simulation")

    normalized = tuple(sorted({_normalize_execution_mode(mode) for mode in execution_modes}))
    if not normalized:
        raise PhysicalMissionTemplateError("execution_modes must include at least one mode")
    return normalized


def _normalize_execution_mode(value: ExecutionMode | str) -> ExecutionMode:
    normalized = _normalize_required(str(value), "execution_mode").lower()
    if normalized not in {"simulation", "live"}:
        raise PhysicalMissionTemplateError("execution_mode must be simulation or live")
    return normalized  # type: ignore[return-value]


def _normalize_tags(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({_normalize_required(value, "tag").lower() for value in values}))


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise PhysicalMissionTemplateError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
