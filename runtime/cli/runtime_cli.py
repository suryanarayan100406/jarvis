"""Developer CLI for run submission, status inspection, stop, and replay."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence, TextIO
from uuid import uuid4

from runtime.pipeline import (
    DeterministicPlanner,
    EchoExecutor,
    PassValidator,
    RunCoordinator,
    RuntimeModuleRegistry,
    SummaryReporter,
    new_run_context,
)
from runtime.replay import RunReplayEndpoint, RunReplayNotFoundError
from runtime.store import LocalRunStore, RunEvent, RunRecord

DEFAULT_DB_PATH = Path("runtime") / "data" / "runs.db"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def main(argv: Sequence[str] | None = None) -> int:
    return run_cli(argv=argv, db_path=None, stdout=None, stderr=None)


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    db_path: str | Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr

    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    store = _build_store(Path(db_path) if db_path is not None else Path(args.db_path))

    if args.command == "submit":
        return _submit_command(args, store, out, err)
    if args.command == "status":
        return _status_command(args, store, out, err)
    if args.command == "stop":
        return _stop_command(args, store, out, err)
    if args.command == "replay":
        return _replay_command(args, store, out, err)

    _emit_json(err, {"error_code": "unknown_command", "message": f"Unsupported command: {args.command}"})
    return 1


def _build_store(db_path: Path) -> LocalRunStore:
    store = LocalRunStore(db_path)
    store.apply_migrations()
    return store


def _submit_command(args: argparse.Namespace, store: LocalRunStore, out: TextIO, err: TextIO) -> int:
    run_id = args.run_id or str(uuid4())

    try:
        store.create_run(run_id=run_id, goal=args.goal, actor_id=args.actor_id, status="created")
    except sqlite3.IntegrityError:
        _emit_json(
            err,
            {
                "error_code": "run_exists",
                "message": f"Run already exists: {run_id}",
                "run_id": run_id,
            },
        )
        return 1

    store.append_event(
        run_id,
        "runtime.run.submitted",
        {"goal": args.goal, "actor_id": args.actor_id},
        severity="info",
    )

    registry = RuntimeModuleRegistry(
        planner=DeterministicPlanner(),
        executor=EchoExecutor(),
        validator=PassValidator(),
        reporter=SummaryReporter(),
    )
    coordinator = RunCoordinator(registry)
    context = new_run_context(goal=args.goal, actor_id=args.actor_id)
    context = type(context)(run_id=run_id, goal=context.goal, actor_id=context.actor_id, created_at=context.created_at)

    try:
        result = coordinator.run(context)

        store.append_event(
            run_id,
            "runtime.plan.completed",
            {
                "plan_id": result.plan.plan_id,
                "task_count": len(result.plan.tasks),
            },
            severity="info",
        )
        store.append_event(
            run_id,
            "runtime.execute.completed",
            {
                "execution_status": result.execution.status,
                "output_count": len(result.execution.outputs),
            },
            severity="info" if result.execution.status == "success" else "warning",
        )
        store.append_event(
            run_id,
            "runtime.validate.completed",
            {
                "passed": result.validation.passed,
                "check_count": len(result.validation.checks),
            },
            severity="info" if result.validation.passed else "warning",
        )
        store.append_event(
            run_id,
            "runtime.report.completed",
            {
                "report_id": result.report.report_id,
                "artifact_count": len(result.report.artifacts),
            },
            severity="info",
        )

        final_status = "completed" if result.execution.status == "success" and result.validation.passed else "failed"
        store.update_run_status(run_id, final_status)
        store.append_event(
            run_id,
            f"runtime.run.{final_status}",
            {
                "transitions": [
                    {"from": transition.from_stage, "to": transition.to_stage, "reason": transition.reason}
                    for transition in result.transitions
                ]
            },
            severity="info" if final_status == "completed" else "error",
        )

        _emit_json(
            out,
            {
                "run_id": run_id,
                "status": final_status,
                "plan_id": result.plan.plan_id,
                "report_id": result.report.report_id,
                "validation_passed": result.validation.passed,
            },
        )
        return 0 if final_status == "completed" else 2
    except Exception as exc:  # pragma: no cover - broad guard for CLI surface
        store.update_run_status(run_id, "failed")
        store.append_event(
            run_id,
            "runtime.run.failed",
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            severity="error",
        )
        _emit_json(
            err,
            {
                "error_code": "submit_failed",
                "message": str(exc),
                "run_id": run_id,
            },
        )
        return 1


def _status_command(args: argparse.Namespace, store: LocalRunStore, out: TextIO, err: TextIO) -> int:
    try:
        run = store.get_run(args.run_id)
    except KeyError:
        _emit_json(
            err,
            {
                "error_code": "run_not_found",
                "message": f"Run not found: {args.run_id}",
                "run_id": args.run_id,
            },
        )
        return 1

    try:
        events = store.list_events(args.run_id, limit=args.limit)
    except ValueError as exc:
        _emit_json(err, {"error_code": "invalid_limit", "message": str(exc)})
        return 1

    _emit_json(
        out,
        {
            "run": _run_to_dict(run),
            "recent_events": [_event_to_dict(event) for event in events],
        },
    )
    return 0


def _stop_command(args: argparse.Namespace, store: LocalRunStore, out: TextIO, err: TextIO) -> int:
    try:
        run = store.get_run(args.run_id)
    except KeyError:
        _emit_json(
            err,
            {
                "error_code": "run_not_found",
                "message": f"Run not found: {args.run_id}",
                "run_id": args.run_id,
            },
        )
        return 1

    if run.status in TERMINAL_STATUSES:
        _emit_json(
            out,
            {
                "run_id": run.run_id,
                "status": run.status,
                "changed": False,
                "message": "Run is already in a terminal state",
            },
        )
        return 0

    store.update_run_status(args.run_id, "cancelled")
    store.append_event(
        args.run_id,
        "runtime.stop.requested",
        {
            "actor_id": args.actor_id,
            "reason": args.reason,
        },
        severity="warning",
    )
    store.append_event(
        args.run_id,
        "runtime.run.cancelled",
        {
            "actor_id": args.actor_id,
            "reason": args.reason,
        },
        severity="warning",
    )

    _emit_json(
        out,
        {
            "run_id": args.run_id,
            "status": "cancelled",
            "changed": True,
            "reason": args.reason,
        },
    )
    return 0


def _replay_command(args: argparse.Namespace, store: LocalRunStore, out: TextIO, err: TextIO) -> int:
    endpoint = RunReplayEndpoint(store)

    try:
        replay = endpoint.replay(
            args.run_id,
            limit=args.limit,
            event_types=args.event_type,
            severities=args.severity,
            include_payload=not args.no_payload,
        )
    except RunReplayNotFoundError:
        _emit_json(
            err,
            {
                "error_code": "run_not_found",
                "message": f"Run not found: {args.run_id}",
                "run_id": args.run_id,
            },
        )
        return 1
    except ValueError as exc:
        _emit_json(err, {"error_code": "invalid_request", "message": str(exc)})
        return 1

    _emit_json(
        out,
        {
            "run": _run_to_dict(replay.run),
            "events": [asdict(event) for event in replay.events],
            "metadata": replay.metadata,
        },
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runtime-cli", description="Runtime orchestration operator CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit_parser = subparsers.add_parser("submit", help="Submit a goal and run deterministic orchestration")
    submit_parser.add_argument("--goal", required=True)
    submit_parser.add_argument("--actor-id", default="boss")
    submit_parser.add_argument("--run-id")
    submit_parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))

    status_parser = subparsers.add_parser("status", help="Inspect run status and recent events")
    status_parser.add_argument("--run-id", required=True)
    status_parser.add_argument("--limit", type=int, default=20)
    status_parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))

    stop_parser = subparsers.add_parser("stop", help="Stop a run and mark it cancelled")
    stop_parser.add_argument("--run-id", required=True)
    stop_parser.add_argument("--actor-id", default="boss")
    stop_parser.add_argument("--reason", default="operator_request")
    stop_parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))

    replay_parser = subparsers.add_parser("replay", help="Replay run history for debugging and audit")
    replay_parser.add_argument("--run-id", required=True)
    replay_parser.add_argument("--limit", type=int)
    replay_parser.add_argument("--event-type", action="append")
    replay_parser.add_argument("--severity", action="append")
    replay_parser.add_argument("--no-payload", action="store_true")
    replay_parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))

    return parser


def _run_to_dict(run: RunRecord) -> dict[str, str]:
    return {
        "run_id": run.run_id,
        "goal": run.goal,
        "actor_id": run.actor_id,
        "status": run.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _event_to_dict(event: RunEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "event_type": event.event_type,
        "payload": event.payload,
        "severity": event.severity,
        "created_at": event.created_at,
    }


def _emit_json(stream: TextIO, payload: dict[str, object]) -> None:
    stream.write(json.dumps(payload, sort_keys=True) + "\n")
