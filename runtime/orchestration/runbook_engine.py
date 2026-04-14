"""Runbook execution engine for routine autonomous workflows."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

RunbookHandler = Callable[["RunbookStep", dict[str, Any]], Any]


@dataclass(frozen=True)
class RunbookStep:
    step_id: str
    action: str
    parameters: dict[str, Any]
    timeout_seconds: float | None = None
    max_attempts: int | None = None
    continue_on_failure: bool = False
    fallback_action: str | None = None
    fallback_parameters: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunbookDefinition:
    runbook_id: str
    name: str
    steps: tuple[RunbookStep, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RunbookStepResult:
    step_id: str
    action: str
    status: str
    attempt_count: int
    output: Any
    error: str | None
    fallback_status: str | None
    fallback_output: Any
    fallback_error: str | None
    started_at: str
    finished_at: str


@dataclass(frozen=True)
class RunbookExecutionResult:
    execution_id: str
    runbook_id: str
    status: str
    step_results: tuple[RunbookStepResult, ...]
    started_at: str
    finished_at: str
    metrics: dict[str, int]


class RunbookEngineError(ValueError):
    """Raised when runbook registration or execution is invalid."""


class RunbookExecutionEngine:
    """Registers runbooks and executes steps with timeout and retry controls."""

    def __init__(
        self,
        handlers: dict[str, RunbookHandler] | None = None,
        *,
        default_timeout_seconds: float = 5.0,
        default_max_attempts: int = 1,
    ) -> None:
        if default_timeout_seconds <= 0:
            raise RunbookEngineError("default_timeout_seconds must be greater than zero")
        if default_max_attempts < 1:
            raise RunbookEngineError("default_max_attempts must be at least 1")

        self.handlers = dict(handlers or {})
        self.default_timeout_seconds = default_timeout_seconds
        self.default_max_attempts = default_max_attempts
        self._runbooks: dict[str, RunbookDefinition] = {}

    def register_runbook(
        self,
        *,
        runbook_id: str,
        name: str,
        steps: list[RunbookStep] | tuple[RunbookStep, ...],
        metadata: dict[str, Any] | None = None,
    ) -> RunbookDefinition:
        normalized_id = _normalize_required(runbook_id, "runbook_id")
        if normalized_id in self._runbooks:
            raise RunbookEngineError(f"Runbook already exists: {normalized_id}")

        normalized_name = _normalize_required(name, "name")
        if not steps:
            raise RunbookEngineError("steps must contain at least one step")

        normalized_steps = tuple(self._normalize_step(step) for step in steps)
        definition = RunbookDefinition(
            runbook_id=normalized_id,
            name=normalized_name,
            steps=normalized_steps,
            metadata=dict(metadata or {}),
        )
        self._runbooks[normalized_id] = definition
        return definition

    def get_runbook(self, runbook_id: str) -> RunbookDefinition:
        normalized_id = _normalize_required(runbook_id, "runbook_id")
        runbook = self._runbooks.get(normalized_id)
        if runbook is None:
            raise KeyError(f"Unknown runbook: {normalized_id}")
        return runbook

    def list_runbooks(self) -> list[RunbookDefinition]:
        runbooks = list(self._runbooks.values())
        runbooks.sort(key=lambda item: item.runbook_id)
        return runbooks

    def execute_runbook(
        self,
        runbook_id: str,
        *,
        context: dict[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> RunbookExecutionResult:
        runbook = self.get_runbook(runbook_id)
        execution_context = dict(context or {})
        assigned_execution_id = _normalize_required(execution_id or str(uuid4()), "execution_id")
        started_at = _utc_now_iso()

        step_results: list[RunbookStepResult] = []
        had_non_blocking_failure = False
        attempts_total = 0
        success_count = 0
        failed_count = 0
        timeout_count = 0
        fallback_count = 0

        for step in runbook.steps:
            result = self._execute_step(step, execution_context)
            step_results.append(result)
            attempts_total += result.attempt_count

            if result.status in {"success", "fallback_succeeded"}:
                success_count += 1
                if result.status == "fallback_succeeded":
                    fallback_count += 1
                    had_non_blocking_failure = True
                execution_context[step.step_id] = result.output
                continue

            if result.status == "timeout":
                timeout_count += 1
            else:
                failed_count += 1

            if step.continue_on_failure:
                had_non_blocking_failure = True
                continue

            status = "timeout" if result.status == "timeout" else "failed"
            return RunbookExecutionResult(
                execution_id=assigned_execution_id,
                runbook_id=runbook.runbook_id,
                status=status,
                step_results=tuple(step_results),
                started_at=started_at,
                finished_at=_utc_now_iso(),
                metrics={
                    "steps_total": len(runbook.steps),
                    "steps_success": success_count,
                    "steps_failed": failed_count,
                    "steps_timed_out": timeout_count,
                    "steps_fallback_used": fallback_count,
                    "attempts_total": attempts_total,
                },
            )

        final_status = "degraded" if had_non_blocking_failure else "success"
        return RunbookExecutionResult(
            execution_id=assigned_execution_id,
            runbook_id=runbook.runbook_id,
            status=final_status,
            step_results=tuple(step_results),
            started_at=started_at,
            finished_at=_utc_now_iso(),
            metrics={
                "steps_total": len(runbook.steps),
                "steps_success": success_count,
                "steps_failed": failed_count,
                "steps_timed_out": timeout_count,
                "steps_fallback_used": fallback_count,
                "attempts_total": attempts_total,
            },
        )

    def _execute_step(self, step: RunbookStep, context: dict[str, Any]) -> RunbookStepResult:
        handler = self.handlers.get(step.action)
        if handler is None:
            now = _utc_now_iso()
            return RunbookStepResult(
                step_id=step.step_id,
                action=step.action,
                status="failed",
                attempt_count=1,
                output=None,
                error=f"No handler registered for action: {step.action}",
                fallback_status=None,
                fallback_output=None,
                fallback_error=None,
                started_at=now,
                finished_at=now,
            )

        max_attempts = step.max_attempts or self.default_max_attempts
        timeout_seconds = step.timeout_seconds or self.default_timeout_seconds
        if max_attempts < 1:
            raise RunbookEngineError(f"Step {step.step_id} has invalid max_attempts")
        if timeout_seconds <= 0:
            raise RunbookEngineError(f"Step {step.step_id} has invalid timeout_seconds")

        started_at = _utc_now_iso()
        last_error: str | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                output = self._run_with_timeout(handler, step, context, timeout_seconds)
                return RunbookStepResult(
                    step_id=step.step_id,
                    action=step.action,
                    status="success",
                    attempt_count=attempt,
                    output=output,
                    error=None,
                    fallback_status=None,
                    fallback_output=None,
                    fallback_error=None,
                    started_at=started_at,
                    finished_at=_utc_now_iso(),
                )
            except TimeoutError:
                last_error = "Step execution timed out"
                if attempt >= max_attempts:
                    return self._finalize_failure(
                        step=step,
                        context=context,
                        status="timeout",
                        attempt_count=attempt,
                        error=last_error,
                        started_at=started_at,
                    )
            except Exception as exc:  # pragma: no cover - boundary guard
                last_error = str(exc)
                if attempt >= max_attempts:
                    return self._finalize_failure(
                        step=step,
                        context=context,
                        status="failed",
                        attempt_count=attempt,
                        error=last_error,
                        started_at=started_at,
                    )

        return self._finalize_failure(
            step=step,
            context=context,
            status="failed",
            attempt_count=max_attempts,
            error=last_error or "Unknown failure",
            started_at=started_at,
        )

    def _finalize_failure(
        self,
        *,
        step: RunbookStep,
        context: dict[str, Any],
        status: str,
        attempt_count: int,
        error: str,
        started_at: str,
    ) -> RunbookStepResult:
        if step.fallback_action:
            fallback_status, fallback_output, fallback_error = self._execute_fallback(step, context)
            if fallback_status == "success":
                return RunbookStepResult(
                    step_id=step.step_id,
                    action=step.action,
                    status="fallback_succeeded",
                    attempt_count=attempt_count,
                    output=fallback_output,
                    error=error,
                    fallback_status=fallback_status,
                    fallback_output=fallback_output,
                    fallback_error=None,
                    started_at=started_at,
                    finished_at=_utc_now_iso(),
                )

            return RunbookStepResult(
                step_id=step.step_id,
                action=step.action,
                status=status,
                attempt_count=attempt_count,
                output=None,
                error=error,
                fallback_status=fallback_status,
                fallback_output=fallback_output,
                fallback_error=fallback_error,
                started_at=started_at,
                finished_at=_utc_now_iso(),
            )

        return RunbookStepResult(
            step_id=step.step_id,
            action=step.action,
            status=status,
            attempt_count=attempt_count,
            output=None,
            error=error,
            fallback_status=None,
            fallback_output=None,
            fallback_error=None,
            started_at=started_at,
            finished_at=_utc_now_iso(),
        )

    def _execute_fallback(self, step: RunbookStep, context: dict[str, Any]) -> tuple[str, Any, str | None]:
        if step.fallback_action is None:
            return ("skipped", None, None)

        fallback_handler = self.handlers.get(step.fallback_action)
        if fallback_handler is None:
            return ("failed", None, f"No handler registered for fallback_action: {step.fallback_action}")

        fallback_step = RunbookStep(
            step_id=f"{step.step_id}:fallback",
            action=step.fallback_action,
            parameters=dict(step.fallback_parameters or step.parameters),
            timeout_seconds=step.timeout_seconds,
            max_attempts=1,
            continue_on_failure=False,
        )
        timeout_seconds = step.timeout_seconds or self.default_timeout_seconds
        if timeout_seconds <= 0:
            return ("failed", None, "Fallback timeout configuration is invalid")

        try:
            output = self._run_with_timeout(fallback_handler, fallback_step, context, timeout_seconds)
            return ("success", output, None)
        except TimeoutError:
            return ("timeout", None, "Fallback execution timed out")
        except Exception as exc:  # pragma: no cover - boundary guard
            return ("failed", None, str(exc))

    @staticmethod
    def _normalize_step(step: RunbookStep) -> RunbookStep:
        normalized_id = _normalize_required(step.step_id, "step_id")
        normalized_action = _normalize_required(step.action, "action")
        return RunbookStep(
            step_id=normalized_id,
            action=normalized_action,
            parameters=dict(step.parameters),
            timeout_seconds=step.timeout_seconds,
            max_attempts=step.max_attempts,
            continue_on_failure=step.continue_on_failure,
            fallback_action=step.fallback_action,
            fallback_parameters=dict(step.fallback_parameters) if step.fallback_parameters else None,
        )

    @staticmethod
    def _run_with_timeout(
        handler: RunbookHandler,
        step: RunbookStep,
        context: dict[str, Any],
        timeout_seconds: float,
    ) -> Any:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(handler, step, context)
            try:
                return future.result(timeout=timeout_seconds)
            except FuturesTimeoutError as exc:
                raise TimeoutError("Step timed out") from exc


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise RunbookEngineError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "RunbookDefinition",
    "RunbookEngineError",
    "RunbookExecutionEngine",
    "RunbookExecutionResult",
    "RunbookStep",
    "RunbookStepResult",
]
