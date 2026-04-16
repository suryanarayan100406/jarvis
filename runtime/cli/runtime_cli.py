"""Developer CLI for run submission, status inspection, stop, and replay."""

from __future__ import annotations

import argparse
import json
import os
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
from runtime.assistant import (
    ActionExecutorAgent,
    AssistantMemoryStore,
    IntentPlannerAgent,
    OllamaResponseEngine,
    OutcomeAuditorAgent,
    StartupBriefingService,
    compose_question_answer,
    compose_reply,
    compose_social_reply,
    detect_current_city,
    normalize_language,
)
from runtime.replay import RunReplayEndpoint, RunReplayNotFoundError
from runtime.store import LocalRunStore, RunEvent, RunRecord
from runtime.voice import ConversationTurnManager, WindowsAudioIO, WindowsAudioIoError

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
    if args.command == "assistant":
        return _assistant_command(args, store, out, err)

    _emit_json(err, {"error_code": "unknown_command", "message": f"Unsupported command: {args.command}"})
    return 1


def _build_store(db_path: Path) -> LocalRunStore:
    store = LocalRunStore(db_path)
    store.apply_migrations()
    return store


def _execute_goal(*, goal: str, actor_id: str, store: LocalRunStore, run_id: str | None = None) -> dict[str, object]:
    normalized_goal = " ".join(str(goal).split())
    if not normalized_goal:
        raise ValueError("goal is required")

    resolved_run_id = run_id or str(uuid4())
    store.create_run(run_id=resolved_run_id, goal=normalized_goal, actor_id=actor_id, status="created")
    store.append_event(
        resolved_run_id,
        "runtime.run.submitted",
        {"goal": normalized_goal, "actor_id": actor_id},
        severity="info",
    )

    registry = RuntimeModuleRegistry(
        planner=DeterministicPlanner(),
        executor=EchoExecutor(),
        validator=PassValidator(),
        reporter=SummaryReporter(),
    )
    coordinator = RunCoordinator(registry)
    context = new_run_context(goal=normalized_goal, actor_id=actor_id)
    context = type(context)(
        run_id=resolved_run_id,
        goal=context.goal,
        actor_id=context.actor_id,
        created_at=context.created_at,
    )

    try:
        result = coordinator.run(context)

        store.append_event(
            resolved_run_id,
            "runtime.plan.completed",
            {
                "plan_id": result.plan.plan_id,
                "task_count": len(result.plan.tasks),
            },
            severity="info",
        )
        store.append_event(
            resolved_run_id,
            "runtime.execute.completed",
            {
                "execution_status": result.execution.status,
                "output_count": len(result.execution.outputs),
            },
            severity="info" if result.execution.status == "success" else "warning",
        )
        store.append_event(
            resolved_run_id,
            "runtime.validate.completed",
            {
                "passed": result.validation.passed,
                "check_count": len(result.validation.checks),
            },
            severity="info" if result.validation.passed else "warning",
        )
        store.append_event(
            resolved_run_id,
            "runtime.report.completed",
            {
                "report_id": result.report.report_id,
                "artifact_count": len(result.report.artifacts),
            },
            severity="info",
        )

        final_status = "completed" if result.execution.status == "success" and result.validation.passed else "failed"
        store.update_run_status(resolved_run_id, final_status)
        store.append_event(
            resolved_run_id,
            f"runtime.run.{final_status}",
            {
                "transitions": [
                    {"from": transition.from_stage, "to": transition.to_stage, "reason": transition.reason}
                    for transition in result.transitions
                ]
            },
            severity="info" if final_status == "completed" else "error",
        )

        return {
            "run_id": resolved_run_id,
            "status": final_status,
            "plan_id": result.plan.plan_id,
            "report_id": result.report.report_id,
            "validation_passed": result.validation.passed,
            "summary": result.report.summary,
        }
    except Exception as exc:  # pragma: no cover - broad guard for CLI surface
        store.update_run_status(resolved_run_id, "failed")
        store.append_event(
            resolved_run_id,
            "runtime.run.failed",
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            severity="error",
        )
        raise RuntimeError(str(exc)) from exc


def _submit_command(args: argparse.Namespace, store: LocalRunStore, out: TextIO, err: TextIO) -> int:
    try:
        payload = _execute_goal(
            goal=args.goal,
            actor_id=args.actor_id,
            store=store,
            run_id=args.run_id,
        )
    except sqlite3.IntegrityError:
        duplicate_run_id = args.run_id or "(auto-generated)"
        _emit_json(
            err,
            {
                "error_code": "run_exists",
                "message": f"Run already exists: {duplicate_run_id}",
                "run_id": duplicate_run_id,
            },
        )
        return 1
    except Exception as exc:
        _emit_json(
            err,
            {
                "error_code": "submit_failed",
                "message": str(exc),
                "run_id": args.run_id,
            },
        )
        return 1

    _emit_json(
        out,
        {
            "run_id": payload["run_id"],
            "status": payload["status"],
            "plan_id": payload["plan_id"],
            "report_id": payload["report_id"],
            "validation_passed": payload["validation_passed"],
        },
    )
    return 0 if payload["status"] == "completed" else 2


def _assistant_command(args: argparse.Namespace, store: LocalRunStore, out: TextIO, err: TextIO) -> int:
    mode = str(args.mode).lower()
    allow_audio = mode in {"audio", "both"}
    allow_text = mode in {"text", "both"}
    language = normalize_language(args.language)
    startup_service = StartupBriefingService()
    llm_engine = OllamaResponseEngine(
        enabled=args.llm_provider in {"auto", "ollama"},
        host=args.ollama_host,
        model=args.ollama_model,
        timeout_seconds=args.ollama_timeout_seconds,
    )
    ollama_warned = False
    ollama_warning_announced = False
    planner_agent = IntentPlannerAgent()
    action_agent = ActionExecutorAgent()
    outcome_agent = OutcomeAuditorAgent()
    memory_store = AssistantMemoryStore(Path("runtime") / "data" / "assistant_memory.json")

    if not args.city.strip() or args.city.lower() == "auto":
        city_pref = memory_store.get_preference("city", "")
        args.city = detect_current_city() or city_pref or "Bengaluru"

    memory_store.set_preference("language", language)
    memory_store.set_preference("city", args.city)
    memory_store.set_preference("news_topic", args.news_topic)
    memory_store.set_preference("llm_provider", args.llm_provider)
    memory_store.set_preference("ollama_model", args.ollama_model)

    voice: WindowsAudioIO | None = None
    if allow_audio:
        voice = WindowsAudioIO(
            speech_rate=args.voice_rate,
            voice_language="hi-IN" if language == "hi" else "en-IN",
            voice_name=args.voice_name,
            stt_timeout_seconds=args.timeout_seconds,
        )
        if not voice.is_supported():
            if mode == "audio":
                _emit_json(
                    err,
                    {
                        "error_code": "audio_not_supported",
                        "message": "Audio mode is only supported on Windows",
                    },
                )
                return 1
            err.write("[WARN] Audio mode not supported on this platform. Falling back to text mode.\n")
            allow_audio = False

    if args.prompt:
        prompt = _normalize_text(args.prompt)
        if not prompt:
            _emit_json(err, {"error_code": "invalid_prompt", "message": "Prompt cannot be empty"})
            return 1

        assistant_text, admin_report, payload, status_code, ollama_warned = _process_assistant_request(
            user_text=prompt,
            actor_id=args.actor_id,
            language=language,
            planner_agent=planner_agent,
            action_agent=action_agent,
            outcome_agent=outcome_agent,
            llm_engine=llm_engine,
            store=store,
            llm_provider=args.llm_provider,
            ollama_warned=ollama_warned,
            memory_store=memory_store,
        )

        if status_code == 1 and payload is None:
            _emit_json(err, {"error_code": "assistant_failed", "message": assistant_text})
            return 1

        if args.llm_provider == "ollama" and ollama_warned and not ollama_warning_announced:
            err.write("[WARN] Ollama response unavailable. Falling back to deterministic replies.\n")
            ollama_warning_announced = True

        enriched_payload = dict(payload or {})
        enriched_payload["assistant_text"] = assistant_text
        enriched_payload["admin_report"] = admin_report
        _emit_json(out, enriched_payload)
        if allow_audio and voice is not None:
            _safe_speak(voice, assistant_text, err)
        return status_code

    if not args.no_banner:
        out.write(_render_jarvis_banner(language=language) + "\n")

    out.write("FRIDAY assistant mode online. Type /help for commands.\n")
    if args.no_startup_brief:
        startup_text = startup_service.build_greeting(actor_id=args.actor_id, language=language)
    else:
        startup_text = startup_service.build_briefing(
            actor_id=args.actor_id,
            language=language,
            city=args.city,
            news_topic=args.news_topic,
            live=_should_use_live_startup_brief(args),
        )
    out.write(f"FRIDAY> {startup_text}\n")
    if allow_audio and voice is not None:
        _safe_speak(voice, startup_text, err)

    turns = ConversationTurnManager()
    last_payload: dict[str, object] | None = None

    while True:
        user_text = ""
        source = "text"

        if allow_text:
            try:
                user_text = _normalize_text(input("You> "))
            except EOFError:
                out.write("\n")
                break

            if user_text.lower() == "/listen" and allow_audio and voice is not None:
                listened = _listen_once(voice, timeout_seconds=args.timeout_seconds, err=err)
                if not listened:
                    continue
                user_text = listened
                source = "voice"
                out.write(f"You (voice)> {user_text}\n")
        else:
            listened = _listen_once(voice, timeout_seconds=args.timeout_seconds, err=err) if voice else None
            if not listened:
                continue
            user_text = listened
            source = "voice"
            out.write(f"You (voice)> {user_text}\n")

        if not user_text:
            continue

        lowered = user_text.lower()
        if lowered in {"/exit", "exit", "quit", "/quit"}:
            break
        if lowered in {"/help", "help"}:
            _print_assistant_help(
                out,
                allow_audio=allow_audio,
                language=language,
                llm_provider=args.llm_provider,
            )
            continue
        if lowered in {"/last", "last"}:
            if last_payload is None:
                out.write("FRIDAY> No previous run metadata is available yet.\n")
                continue
            out.write(
                "FRIDAY> "
                + f"last run_id={last_payload['run_id']} status={last_payload['status']} plan_id={last_payload['plan_id']}"
                + "\n"
            )
            continue
        if lowered in {"/todos", "todos"}:
            open_todos = memory_store.list_open_todos(limit=10)
            if not open_todos:
                out.write("FRIDAY> No open todos right now.\n")
            else:
                out.write("FRIDAY> Open todos:\n")
                for index, todo in enumerate(open_todos, start=1):
                    out.write(f"  {index}. {todo.text}\n")
            continue
        if lowered.startswith("/done "):
            raw_index = lowered.replace("/done", "", 1).strip()
            try:
                todo_index = int(raw_index)
            except ValueError:
                out.write("FRIDAY> Use /done <number>.\n")
                continue
            completed = memory_store.close_todo_by_index(todo_index)
            if completed is None:
                out.write("FRIDAY> I could not find that todo number.\n")
            else:
                out.write(f"FRIDAY> Closed todo: {completed.text}\n")
            continue

        if lowered in {"hi", "hello", "hey", "hey friday", "good morning", "good evening"}:
            if language == "hi":
                assistant_text = f"Namaste {args.actor_id}. Main online hoon aur ready hoon."
            else:
                assistant_text = f"Hello {args.actor_id}. I am online and ready."
            out.write(f"FRIDAY> {assistant_text}\n")
            if allow_audio and voice is not None:
                _safe_speak(voice, assistant_text, err)
            continue

        turn = turns.start_turn(user_input=user_text, source=source)
        turns.begin_response(turn.turn_id)

        try:
            assistant_text, admin_report, payload, status_code, ollama_warned = _process_assistant_request(
                user_text=user_text,
                actor_id=args.actor_id,
                language=language,
                planner_agent=planner_agent,
                action_agent=action_agent,
                outcome_agent=outcome_agent,
                llm_engine=llm_engine,
                store=store,
                llm_provider=args.llm_provider,
                ollama_warned=ollama_warned,
                memory_store=memory_store,
            )
            turns.append_response(turn.turn_id, assistant_text)
            turns.complete(turn.turn_id)
            if payload is not None:
                last_payload = payload

            if args.llm_provider == "ollama" and ollama_warned and not ollama_warning_announced:
                err.write("[WARN] Ollama response unavailable. Falling back to deterministic replies.\n")
                ollama_warning_announced = True

            out.write(f"FRIDAY> {assistant_text}\n")
            out.write(f"ADMIN> {admin_report}\n")
            if args.show_metadata and payload is not None:
                out.write(f"[run_id: {payload['run_id']} | status: {payload['status']}]\n")

            if allow_audio and voice is not None:
                _safe_speak(voice, assistant_text, err)

            if status_code == 2 and payload is not None and args.show_metadata:
                out.write("[WARN] Goal execution did not complete successfully.\n")
        except Exception as exc:
            turns.cancel(turn.turn_id)
            assistant_text = f"I could not complete that request. Details: {exc}"
            out.write(f"FRIDAY> {assistant_text}\n")
            if allow_audio and voice is not None:
                _safe_speak(voice, assistant_text, err)
            continue

    out.write("Session closed.\n")
    return 0


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

    assistant_parser = subparsers.add_parser(
        "assistant",
        help="Start an interactive assistant session with text and optional audio",
    )
    assistant_parser.add_argument("--mode", choices=("text", "audio", "both"), default="text")
    assistant_parser.add_argument("--actor-id", default="boss")
    assistant_parser.add_argument("--prompt", help="Run a single assistant prompt and exit")
    assistant_parser.add_argument("--timeout-seconds", type=int, default=8)
    assistant_parser.add_argument("--voice-rate", type=int, default=0)
    assistant_parser.add_argument("--voice-name")
    assistant_parser.add_argument("--language", choices=("hi", "en"), default="hi")
    assistant_parser.add_argument("--city", default="auto")
    assistant_parser.add_argument("--news-topic", default="India technology")
    assistant_parser.add_argument("--no-banner", action="store_true")
    assistant_parser.add_argument("--no-startup-brief", action="store_true")
    assistant_parser.add_argument(
        "--llm-provider",
        choices=("auto", "deterministic", "ollama"),
        default="auto",
    )
    assistant_parser.add_argument(
        "--ollama-model",
        default=os.getenv("FRIDAY_OLLAMA_MODEL", "gemma4:latest"),
    )
    assistant_parser.add_argument("--ollama-host", default="http://127.0.0.1:11434")
    assistant_parser.add_argument("--ollama-timeout-seconds", type=int, default=6)
    assistant_parser.add_argument("--show-metadata", action="store_true")
    assistant_parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))

    return parser


def _listen_once(voice: WindowsAudioIO, *, timeout_seconds: int, err: TextIO) -> str | None:
    try:
        transcript = voice.listen_once(timeout_seconds=timeout_seconds)
    except WindowsAudioIoError as exc:
        err.write(f"[WARN] Voice input unavailable: {exc}\n")
        return None

    if transcript is None:
        err.write("[WARN] No voice input detected.\n")
        return None
    return transcript


def _safe_speak(voice: WindowsAudioIO, text: str, err: TextIO) -> None:
    try:
        voice.speak(text)
    except WindowsAudioIoError as exc:
        err.write(f"[WARN] Voice output unavailable: {exc}\n")


def _print_assistant_help(stream: TextIO, *, allow_audio: bool, language: str, llm_provider: str) -> None:
    stream.write("Commands:\n")
    stream.write("  /help   show this help\n")
    stream.write("  /exit   close assistant mode\n")
    stream.write("  /last   show metadata for the previous run\n")
    stream.write("  /todos  list open todos\n")
    stream.write("  /done N mark todo number N as complete\n")
    if allow_audio:
        stream.write("  /listen capture one spoken input in mixed mode\n")
    stream.write("Behavior: questions get direct answers, commands execute with audited outcomes.\n")
    stream.write(f"Profile: language={language}, llm_provider={llm_provider}\n")


def _render_jarvis_banner(*, language: str) -> str:
    if language == "hi":
        title = "FRIDAY // AI Command Deck"
        subtitle = "Mode: Hinglish conversational assistant"
    else:
        title = "FRIDAY // AI Command Deck"
        subtitle = "Mode: English conversational assistant"

    line = "=" * 56
    return "\n".join([line, title, subtitle, line])


def _render_assistant_response(
    *,
    user_text: str,
    payload: dict[str, object],
    actor_id: str,
    language: str,
) -> str:
    return compose_reply(
        user_text=user_text,
        payload=payload,
        actor_id=actor_id,
        language=language,
    )


def _process_assistant_request(
    *,
    user_text: str,
    actor_id: str,
    language: str,
    planner_agent: IntentPlannerAgent,
    action_agent: ActionExecutorAgent,
    outcome_agent: OutcomeAuditorAgent,
    llm_engine: OllamaResponseEngine,
    store: LocalRunStore,
    llm_provider: str,
    ollama_warned: bool,
    memory_store: AssistantMemoryStore,
) -> tuple[str, str, dict[str, object] | None, int, bool]:
    decision = planner_agent.decide(user_text)

    if decision.intent_type == "chat":
        chat_text = compose_social_reply(user_text=user_text, actor_id=actor_id, language=language)
        chat_payload = {
            "run_id": "chat-local",
            "status": "completed",
            "plan_id": "chat-response",
            "report_id": "chat-response",
            "validation_passed": True,
            "summary": "Conversational response delivered.",
        }
        llm_text = llm_engine.generate_reply(
            user_text=user_text,
            actor_id=actor_id,
            language=language,
            payload=chat_payload,
        )
        if llm_text:
            chat_text = llm_text
        elif llm_provider == "ollama" and not ollama_warned:
            ollama_warned = True

        admin_report = outcome_agent.build_report(
            decision=decision,
            language=language,
            question_answer=chat_text,
        )
        return chat_text, admin_report, chat_payload, 0, ollama_warned

    if decision.intent_type == "memory":
        memory_store.add_note(user_text)
        todo_text = _extract_todo_text(user_text)
        if todo_text:
            memory_store.add_todo(todo_text)
        assistant_text = (
            "Note saved. I will remember this preference and keep it in your working context."
            if language == "en"
            else "Note save kar diya. Main is preference ko yaad rakhunga."
        )
        admin_report = outcome_agent.build_report(decision=decision, language=language)
        payload = {
            "run_id": "memory-local",
            "status": "completed",
            "plan_id": "memory-capture",
            "report_id": "memory-capture",
            "validation_passed": True,
            "summary": "Memory preference captured.",
        }
        return assistant_text, admin_report, payload, 0, ollama_warned

    if decision.intent_type == "question":
        answer = compose_question_answer(user_text=user_text, actor_id=actor_id, language=language)
        question_payload = {
            "run_id": "question-local",
            "status": "completed",
            "plan_id": "question-answer",
            "report_id": "question-answer",
            "validation_passed": True,
            "summary": "Direct question answered.",
        }
        llm_text = llm_engine.generate_reply(
            user_text=user_text,
            actor_id=actor_id,
            language=language,
            payload=question_payload,
        )
        if llm_text:
            answer = llm_text
        elif llm_provider == "ollama" and not ollama_warned:
            ollama_warned = True

        admin_report = outcome_agent.build_report(
            decision=decision,
            language=language,
            question_answer=answer,
        )
        return answer, admin_report, question_payload, 0, ollama_warned

    if decision.intent_type == "command":
        action_result = action_agent.execute(decision)
        if language == "hi":
            if action_result.success:
                assistant_text = f"Command execute ho gaya: {action_result.summary}."
            else:
                assistant_text = f"Command complete nahi hua: {action_result.summary}."
        else:
            if action_result.success:
                assistant_text = f"Command executed: {action_result.summary}."
            else:
                assistant_text = f"Command did not complete: {action_result.summary}."

        command_payload = {
            "run_id": "command-local",
            "status": "completed" if action_result.success else "failed",
            "plan_id": decision.action or "command",
            "report_id": "command-outcome",
            "validation_passed": action_result.success,
            "summary": action_result.summary,
        }

        llm_text = llm_engine.generate_reply(
            user_text=user_text,
            actor_id=actor_id,
            language=language,
            payload=command_payload,
        )
        if llm_text:
            assistant_text = llm_text
        elif llm_provider == "ollama" and not ollama_warned:
            ollama_warned = True

        admin_report = outcome_agent.build_report(
            decision=decision,
            language=language,
            action_result=action_result,
        )
        return assistant_text, admin_report, command_payload, (0 if action_result.success else 2), ollama_warned

    try:
        payload = _execute_goal(goal=user_text, actor_id=actor_id, store=store)
    except Exception as exc:
        return str(exc), "Intent: goal | Outcome: failed to execute deterministic pipeline.", None, 1, ollama_warned

    assistant_text = _render_assistant_response(
        user_text=user_text,
        payload=payload,
        actor_id=actor_id,
        language=language,
    )
    llm_text = llm_engine.generate_reply(
        user_text=user_text,
        actor_id=actor_id,
        language=language,
        payload=payload,
    )
    if llm_text:
        assistant_text = llm_text
    elif llm_provider == "ollama" and not ollama_warned:
        ollama_warned = True

    admin_report = outcome_agent.build_report(
        decision=decision,
        language=language,
        pipeline_payload=payload,
    )
    return assistant_text, admin_report, payload, (0 if payload["status"] == "completed" else 2), ollama_warned


def _extract_todo_text(user_text: str) -> str:
    normalized = _normalize_text(user_text)
    lowered = normalized.lower()
    for prefix in ("todo ", "remember to ", "note that "):
        if lowered.startswith(prefix):
            return normalized[len(prefix) :].strip()
    return ""


def _should_use_live_startup_brief(args: argparse.Namespace) -> bool:
    if args.no_startup_brief:
        return False
    if os.getenv("FRIDAY_FORCE_LIVE_BRIEF", "").strip() == "1":
        return True
    return True


def _normalize_text(text: str) -> str:
    return " ".join(str(text).split())


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
