"""Rollback actions for service restart and deploy routines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from .connector_manager import ConnectorExecutionResult, ConnectorManager

RollbackStatus = Literal["dry_run", "completed", "rolled_back", "rollback_failed"]
RoutineType = Literal["service_restart", "deploy"]


@dataclass(frozen=True)
class RollbackAction:
    action_id: str
    operation: str
    payload: dict[str, Any]
    description: str


@dataclass(frozen=True)
class RollbackRoutinePlan:
    routine_id: str
    routine_type: RoutineType
    host_id: str
    adapter_name: str | None
    identity: str | None
    forward_action: RollbackAction
    rollback_actions: tuple[RollbackAction, ...]
    created_at: str


@dataclass(frozen=True)
class RollbackRoutineResult:
    routine_id: str
    routine_type: RoutineType
    host_id: str
    status: RollbackStatus
    forward_result: ConnectorExecutionResult | None
    rollback_results: tuple[ConnectorExecutionResult, ...]
    error: str | None


class RollbackActionError(ValueError):
    """Raised when rollback plan creation or execution fails irrecoverably."""


class RollbackActionManager:
    """Builds and executes rollback-capable service restart and deploy routines."""

    def __init__(self, connector_manager: ConnectorManager) -> None:
        self.connector_manager = connector_manager
        self._plans: dict[str, RollbackRoutinePlan] = {}

    def build_service_restart_plan(
        self,
        *,
        host_id: str,
        service: str,
        adapter_name: str | None = None,
        identity: str | None = None,
    ) -> RollbackRoutinePlan:
        self.connector_manager.inventory.get_host(host_id)
        normalized_service = _normalize_required(service, "service")
        normalized_host_id = _normalize_required(host_id, "host_id")

        routine_id = str(uuid4())
        forward_action = RollbackAction(
            action_id=str(uuid4()),
            operation="restart_service",
            payload={
                "service": normalized_service,
                "command": f"systemctl restart {normalized_service}",
            },
            description=f"Restart service {normalized_service}",
        )
        rollback_action = RollbackAction(
            action_id=str(uuid4()),
            operation="recover_service",
            payload={
                "service": normalized_service,
                "command": f"systemctl start {normalized_service}",
            },
            description=f"Rollback restart by starting service {normalized_service}",
        )

        plan = RollbackRoutinePlan(
            routine_id=routine_id,
            routine_type="service_restart",
            host_id=normalized_host_id,
            adapter_name=_normalize_optional(adapter_name),
            identity=_normalize_optional(identity),
            forward_action=forward_action,
            rollback_actions=(rollback_action,),
            created_at=_utc_now_iso(),
        )
        self._plans[routine_id] = plan
        return plan

    def build_deploy_plan(
        self,
        *,
        host_id: str,
        release_id: str,
        previous_release_id: str,
        adapter_name: str | None = None,
        identity: str | None = None,
    ) -> RollbackRoutinePlan:
        self.connector_manager.inventory.get_host(host_id)
        normalized_host_id = _normalize_required(host_id, "host_id")
        normalized_release = _normalize_required(release_id, "release_id")
        normalized_previous_release = _normalize_required(previous_release_id, "previous_release_id")

        routine_id = str(uuid4())
        forward_action = RollbackAction(
            action_id=str(uuid4()),
            operation="deploy_release",
            payload={
                "release_id": normalized_release,
                "command": f"deployctl deploy {normalized_release}",
            },
            description=f"Deploy release {normalized_release}",
        )
        rollback_action = RollbackAction(
            action_id=str(uuid4()),
            operation="recover_release",
            payload={
                "release_id": normalized_previous_release,
                "command": f"deployctl rollback {normalized_previous_release}",
            },
            description=f"Rollback deployment to release {normalized_previous_release}",
        )

        plan = RollbackRoutinePlan(
            routine_id=routine_id,
            routine_type="deploy",
            host_id=normalized_host_id,
            adapter_name=_normalize_optional(adapter_name),
            identity=_normalize_optional(identity),
            forward_action=forward_action,
            rollback_actions=(rollback_action,),
            created_at=_utc_now_iso(),
        )
        self._plans[routine_id] = plan
        return plan

    def get_plan(self, routine_id: str) -> RollbackRoutinePlan:
        normalized_routine_id = _normalize_required(routine_id, "routine_id")
        plan = self._plans.get(normalized_routine_id)
        if plan is None:
            raise KeyError(f"Unknown rollback routine: {normalized_routine_id}")
        return plan

    def execute_plan(self, plan: RollbackRoutinePlan, *, dry_run: bool = False) -> RollbackRoutineResult:
        if dry_run:
            return RollbackRoutineResult(
                routine_id=plan.routine_id,
                routine_type=plan.routine_type,
                host_id=plan.host_id,
                status="dry_run",
                forward_result=None,
                rollback_results=(),
                error=None,
            )

        forward_result: ConnectorExecutionResult | None = None
        rollback_results: list[ConnectorExecutionResult] = []

        try:
            forward_result = self.connector_manager.execute(
                plan.host_id,
                plan.forward_action.operation,
                payload=plan.forward_action.payload,
                adapter_name=plan.adapter_name,
                identity=plan.identity,
            )
        except Exception as forward_error:
            rollback_error: Exception | None = None
            for action in plan.rollback_actions:
                try:
                    rollback_results.append(
                        self.connector_manager.execute(
                            plan.host_id,
                            action.operation,
                            payload=action.payload,
                            adapter_name=plan.adapter_name,
                            identity=plan.identity,
                        )
                    )
                except Exception as exc:
                    rollback_error = exc
                    break

            if rollback_error is not None:
                return RollbackRoutineResult(
                    routine_id=plan.routine_id,
                    routine_type=plan.routine_type,
                    host_id=plan.host_id,
                    status="rollback_failed",
                    forward_result=forward_result,
                    rollback_results=tuple(rollback_results),
                    error=(
                        f"Forward action failed: {type(forward_error).__name__}: {forward_error}; "
                        f"rollback failed: {type(rollback_error).__name__}: {rollback_error}"
                    ),
                )

            return RollbackRoutineResult(
                routine_id=plan.routine_id,
                routine_type=plan.routine_type,
                host_id=plan.host_id,
                status="rolled_back",
                forward_result=forward_result,
                rollback_results=tuple(rollback_results),
                error=f"Forward action failed: {type(forward_error).__name__}: {forward_error}",
            )

        return RollbackRoutineResult(
            routine_id=plan.routine_id,
            routine_type=plan.routine_type,
            host_id=plan.host_id,
            status="completed",
            forward_result=forward_result,
            rollback_results=(),
            error=None,
        )

    def run_service_restart(
        self,
        *,
        host_id: str,
        service: str,
        adapter_name: str | None = None,
        identity: str | None = None,
        dry_run: bool = False,
    ) -> RollbackRoutineResult:
        plan = self.build_service_restart_plan(
            host_id=host_id,
            service=service,
            adapter_name=adapter_name,
            identity=identity,
        )
        return self.execute_plan(plan, dry_run=dry_run)

    def run_deploy(
        self,
        *,
        host_id: str,
        release_id: str,
        previous_release_id: str,
        adapter_name: str | None = None,
        identity: str | None = None,
        dry_run: bool = False,
    ) -> RollbackRoutineResult:
        plan = self.build_deploy_plan(
            host_id=host_id,
            release_id=release_id,
            previous_release_id=previous_release_id,
            adapter_name=adapter_name,
            identity=identity,
        )
        return self.execute_plan(plan, dry_run=dry_run)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise RollbackActionError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
