"""Shared data models for runtime module boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RunContext:
    run_id: str
    goal: str
    actor_id: str
    created_at: str = field(default_factory=_utc_now_iso)


@dataclass(frozen=True)
class PlannedTask:
    task_id: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanResult:
    plan_id: str
    tasks: list[PlannedTask]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    outputs: list[dict[str, Any]]
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    checks: list[dict[str, Any]]
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReportResult:
    report_id: str
    summary: str
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def new_run_context(goal: str, actor_id: str) -> RunContext:
    return RunContext(run_id=str(uuid4()), goal=goal, actor_id=actor_id)
