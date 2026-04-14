"""Executor engine with timeout, retry, and cancellation hooks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Callable

from runtime.pipeline.models import ExecutionResult, PlanResult, PlannedTask, RunContext


TaskRunner = Callable[[PlannedTask, RunContext], Any]
RetryHook = Callable[[str, int, str], None]
TimeoutHook = Callable[[str, int], None]
CancelHook = Callable[[str], None]
CancelChecker = Callable[[], bool]


class ExecutorEngine:
    """Executes planned tasks with retry, timeout, and cancellation controls."""

    def __init__(
        self,
        task_runner: TaskRunner | None = None,
        timeout_seconds: float = 5.0,
        max_attempts: int = 1,
        cancellation_checker: CancelChecker | None = None,
        on_retry: RetryHook | None = None,
        on_timeout: TimeoutHook | None = None,
        on_cancel: CancelHook | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        self.task_runner = task_runner or self._default_task_runner
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.cancellation_checker = cancellation_checker
        self.on_retry = on_retry
        self.on_timeout = on_timeout
        self.on_cancel = on_cancel

    def execute(self, context: RunContext, plan: PlanResult) -> ExecutionResult:
        outputs: list[dict[str, Any]] = []
        total_attempts = 0

        for task in plan.tasks:
            if self._is_cancelled():
                if self.on_cancel:
                    self.on_cancel(task.task_id)
                return ExecutionResult(
                    status="cancelled",
                    outputs=outputs,
                    metrics={"attempts": total_attempts, "task_count": len(outputs)},
                )

            task_result, task_attempts, failure_status = self._execute_task_with_retries(context, task)
            total_attempts += task_attempts
            outputs.append(task_result)

            if failure_status is not None:
                return ExecutionResult(
                    status=failure_status,
                    outputs=outputs,
                    metrics={"attempts": total_attempts, "task_count": len(outputs)},
                )

        return ExecutionResult(
            status="success",
            outputs=outputs,
            metrics={"attempts": total_attempts, "task_count": len(plan.tasks)},
        )

    def _execute_task_with_retries(
        self, context: RunContext, task: PlannedTask
    ) -> tuple[dict[str, Any], int, str | None]:
        attempts = 0

        while attempts < self.max_attempts:
            attempts += 1
            try:
                result = self._run_with_timeout(task, context)
                return (
                    {
                        "task_id": task.task_id,
                        "status": "success",
                        "attempt": attempts,
                        "result": result,
                    },
                    attempts,
                    None,
                )
            except TimeoutError:
                if self.on_timeout:
                    self.on_timeout(task.task_id, attempts)
                if attempts >= self.max_attempts:
                    return (
                        {
                            "task_id": task.task_id,
                            "status": "timeout",
                            "attempt": attempts,
                            "error": "Task timed out",
                        },
                        attempts,
                        "timeout",
                    )
                if self.on_retry:
                    self.on_retry(task.task_id, attempts, "timeout")
            except Exception as exc:  # pragma: no cover - broad by design for boundary protection
                if attempts >= self.max_attempts:
                    return (
                        {
                            "task_id": task.task_id,
                            "status": "failed",
                            "attempt": attempts,
                            "error": str(exc),
                        },
                        attempts,
                        "failed",
                    )
                if self.on_retry:
                    self.on_retry(task.task_id, attempts, str(exc))

        return (
            {
                "task_id": task.task_id,
                "status": "failed",
                "attempt": attempts,
                "error": "Max attempts exhausted",
            },
            attempts,
            "failed",
        )

    def _run_with_timeout(self, task: PlannedTask, context: RunContext) -> Any:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.task_runner, task, context)
            try:
                return future.result(timeout=self.timeout_seconds)
            except FuturesTimeoutError as exc:
                raise TimeoutError("Task execution timed out") from exc

    def _is_cancelled(self) -> bool:
        return bool(self.cancellation_checker and self.cancellation_checker())

    @staticmethod
    def _default_task_runner(task: PlannedTask, _context: RunContext) -> dict[str, str]:
        return {"echo": task.description}
