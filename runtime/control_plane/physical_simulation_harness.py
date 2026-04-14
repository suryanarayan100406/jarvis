"""Simulation harness for physical motion and actuation plans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from .physical_device_registry import PhysicalDeviceRegistry
from .physical_connector_sdk import PhysicalConnectorSDK

ExecutionMode = Literal["simulation", "live"]
StepStatus = Literal["success", "failed", "skipped"]


@dataclass(frozen=True)
class PhysicalPlanStep:
    device_id: str
    capability_id: str
    payload: dict[str, Any] | None = None
    identity: str | None = None


@dataclass(frozen=True)
class PhysicalPlanStepResult:
    sequence: int
    mode: ExecutionMode
    device_id: str
    connector_id: str
    capability_id: str
    capability_type: str
    risk_tier: str
    requires_sandbox_approval: bool
    status: StepStatus
    result: dict[str, Any] | None
    error: str | None


@dataclass(frozen=True)
class PhysicalPlanExecutionResult:
    plan_id: str
    mode: ExecutionMode
    executed_at: str
    total_steps: int
    succeeded: int
    failed: int
    skipped: int
    requires_sandbox_approval: bool
    ready_for_live: bool
    plan_token: str
    results: tuple[PhysicalPlanStepResult, ...]


class PhysicalSimulationHarnessError(ValueError):
    """Raised when simulation workflow constraints are violated."""


class PhysicalSimulationHarness:
    """Executes simulation plans and enforces simulation-first live promotion."""

    def __init__(
        self,
        connector_sdk: PhysicalConnectorSDK,
        device_registry: PhysicalDeviceRegistry,
    ) -> None:
        self.connector_sdk = connector_sdk
        self.device_registry = device_registry
        self._approved_simulation_tokens: dict[str, str] = {}

    def simulate_plan(
        self,
        plan_id: str,
        steps: list[PhysicalPlanStep] | tuple[PhysicalPlanStep, ...],
        *,
        default_identity: str | None = None,
        fail_fast: bool = False,
    ) -> PhysicalPlanExecutionResult:
        return self._execute_plan(
            mode="simulation",
            plan_id=plan_id,
            steps=steps,
            default_identity=default_identity,
            fail_fast=fail_fast,
            sandbox_approved=False,
        )

    def execute_live_plan(
        self,
        plan_id: str,
        steps: list[PhysicalPlanStep] | tuple[PhysicalPlanStep, ...],
        *,
        sandbox_approved: bool,
        default_identity: str | None = None,
        fail_fast: bool = False,
    ) -> PhysicalPlanExecutionResult:
        normalized_plan_id = _normalize_required(plan_id, "plan_id")
        normalized_steps = _normalize_steps(steps)
        token = _build_plan_token(normalized_plan_id, normalized_steps)

        approved_token = self._approved_simulation_tokens.get(normalized_plan_id)
        if approved_token is None:
            raise PhysicalSimulationHarnessError(
                f"Plan {normalized_plan_id} has no approved simulation run"
            )
        if approved_token != token:
            raise PhysicalSimulationHarnessError(
                f"Plan {normalized_plan_id} changed after simulation approval"
            )

        if self._plan_requires_sandbox_approval(normalized_steps) and not sandbox_approved:
            raise PhysicalSimulationHarnessError(
                f"Live execution requires sandbox approval for plan {normalized_plan_id}"
            )

        # Promote simulation approvals as single-use to block replay without re-simulation.
        self._approved_simulation_tokens.pop(normalized_plan_id, None)

        return self._execute_plan(
            mode="live",
            plan_id=normalized_plan_id,
            steps=normalized_steps,
            default_identity=default_identity,
            fail_fast=fail_fast,
            sandbox_approved=sandbox_approved,
        )

    def _execute_plan(
        self,
        *,
        mode: ExecutionMode,
        plan_id: str,
        steps: list[PhysicalPlanStep] | tuple[PhysicalPlanStep, ...],
        default_identity: str | None,
        fail_fast: bool,
        sandbox_approved: bool,
    ) -> PhysicalPlanExecutionResult:
        normalized_plan_id = _normalize_required(plan_id, "plan_id")
        normalized_steps = _normalize_steps(steps)
        normalized_default_identity = (
            _normalize_required(default_identity, "default_identity")
            if default_identity is not None
            else None
        )

        plan_token = _build_plan_token(normalized_plan_id, normalized_steps)
        stop_remaining = False
        requires_sandbox_approval = False
        step_results: list[PhysicalPlanStepResult] = []

        for index, step in enumerate(normalized_steps, start=1):
            device_id = step.device_id
            capability_id = step.capability_id
            connector_id = ""
            capability_type = ""
            risk_tier = ""
            step_requires_sandbox = False

            if stop_remaining:
                step_results.append(
                    PhysicalPlanStepResult(
                        sequence=index,
                        mode=mode,
                        device_id=device_id,
                        connector_id=connector_id,
                        capability_id=capability_id,
                        capability_type=capability_type,
                        risk_tier=risk_tier,
                        requires_sandbox_approval=step_requires_sandbox,
                        status="skipped",
                        result=None,
                        error="Skipped because fail_fast was triggered by a previous failure",
                    )
                )
                continue

            try:
                device = self.device_registry.get_device(device_id)
                if not device.enabled:
                    raise PhysicalSimulationHarnessError(
                        f"Device {device_id} is disabled"
                    )

                connector_id = device.connector_id
                capability = self.device_registry.get_capability_profile(device_id, capability_id)
                capability_type = capability.capability_type
                risk_tier = capability.risk_tier
                step_requires_sandbox = capability.requires_sandbox_approval
                requires_sandbox_approval = requires_sandbox_approval or step_requires_sandbox

                identity = step.identity or normalized_default_identity
                execution = self.connector_sdk.execute(
                    connector_id=device.connector_id,
                    capability_id=capability.capability_id,
                    payload=step.payload,
                    simulation=(mode == "simulation"),
                    sandbox_approved=sandbox_approved,
                    identity=identity,
                )

                step_results.append(
                    PhysicalPlanStepResult(
                        sequence=index,
                        mode=mode,
                        device_id=device.device_id,
                        connector_id=device.connector_id,
                        capability_id=capability.capability_id,
                        capability_type=capability.capability_type,
                        risk_tier=capability.risk_tier,
                        requires_sandbox_approval=capability.requires_sandbox_approval,
                        status="success",
                        result=dict(execution.result),
                        error=None,
                    )
                )
            except Exception as exc:
                step_results.append(
                    PhysicalPlanStepResult(
                        sequence=index,
                        mode=mode,
                        device_id=device_id,
                        connector_id=connector_id,
                        capability_id=capability_id,
                        capability_type=capability_type,
                        risk_tier=risk_tier,
                        requires_sandbox_approval=step_requires_sandbox,
                        status="failed",
                        result=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                if fail_fast:
                    stop_remaining = True

        succeeded = sum(1 for result in step_results if result.status == "success")
        failed = sum(1 for result in step_results if result.status == "failed")
        skipped = sum(1 for result in step_results if result.status == "skipped")
        ready_for_live = mode == "simulation" and failed == 0 and skipped == 0

        if mode == "simulation":
            if ready_for_live:
                self._approved_simulation_tokens[normalized_plan_id] = plan_token
            else:
                self._approved_simulation_tokens.pop(normalized_plan_id, None)

        return PhysicalPlanExecutionResult(
            plan_id=normalized_plan_id,
            mode=mode,
            executed_at=_utc_now_iso(),
            total_steps=len(normalized_steps),
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            requires_sandbox_approval=requires_sandbox_approval,
            ready_for_live=ready_for_live,
            plan_token=plan_token,
            results=tuple(step_results),
        )

    def _plan_requires_sandbox_approval(
        self,
        steps: tuple[PhysicalPlanStep, ...],
    ) -> bool:
        for step in steps:
            capability = self.device_registry.get_capability_profile(step.device_id, step.capability_id)
            if capability.requires_sandbox_approval:
                return True
        return False


def _normalize_steps(
    steps: list[PhysicalPlanStep] | tuple[PhysicalPlanStep, ...],
) -> tuple[PhysicalPlanStep, ...]:
    normalized_steps: list[PhysicalPlanStep] = []
    for step in steps:
        if not isinstance(step, PhysicalPlanStep):
            raise TypeError("steps must contain PhysicalPlanStep entries")

        normalized_steps.append(
            PhysicalPlanStep(
                device_id=_normalize_required(step.device_id, "device_id").lower(),
                capability_id=_normalize_required(step.capability_id, "capability_id").lower(),
                payload=dict(step.payload or {}),
                identity=(
                    _normalize_required(step.identity, "identity")
                    if step.identity is not None
                    else None
                ),
            )
        )

    if not normalized_steps:
        raise PhysicalSimulationHarnessError("steps must contain at least one plan step")

    return tuple(normalized_steps)


def _build_plan_token(plan_id: str, steps: tuple[PhysicalPlanStep, ...]) -> str:
    canonical_steps = [
        {
            "device_id": step.device_id,
            "capability_id": step.capability_id,
            "payload": step.payload,
            "identity": step.identity,
        }
        for step in steps
    ]
    canonical = json.dumps(
        {
            "plan_id": plan_id,
            "steps": canonical_steps,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"sim-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise PhysicalSimulationHarnessError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
