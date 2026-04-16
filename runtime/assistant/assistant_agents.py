"""Intent routing and bounded desktop action agents for assistant mode."""

from __future__ import annotations

import os
import re
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from urllib.request import urlretrieve


IntentType = Literal["question", "command", "goal", "memory", "chat"]


@dataclass(frozen=True)
class IntentDecision:
    intent_type: IntentType
    confidence: float
    reason: str
    action: str | None
    arguments: dict[str, str]


@dataclass(frozen=True)
class ActionResult:
    action: str
    success: bool
    executed: bool
    summary: str
    details: tuple[str, ...]


class IntentPlannerAgent:
    """Classifies user inputs into questions, commands, memory requests, or orchestration goals."""

    _question_prefixes = (
        "what",
        "why",
        "how",
        "when",
        "where",
        "who",
        "which",
        "can",
        "could",
        "would",
        "should",
        "is",
        "are",
        "do",
        "does",
        "did",
    )
    _chat_phrases = {
        "ji",
        "haan",
        "han",
        "hmm",
        "hmmm",
        "ok",
        "okay",
        "theek",
        "thik",
        "accha",
        "acha",
        "yes",
        "yeah",
        "yo",
        "thanks",
        "thank you",
    }

    def decide(self, user_text: str) -> IntentDecision:
        normalized = _normalize(user_text)
        lowered = normalized.lower()

        if lowered.startswith(("remember ", "remember that ", "note that ", "todo ")):
            return IntentDecision(
                intent_type="memory",
                confidence=0.96,
                reason="Explicit memory or todo phrase detected",
                action="memory.capture",
                arguments={"text": normalized},
            )

        command_match = self._detect_command(lowered)
        if command_match is not None:
            action, arguments, reason = command_match
            return IntentDecision(
                intent_type="command",
                confidence=0.94,
                reason=reason,
                action=action,
                arguments=arguments,
            )

        if self._is_small_talk(lowered):
            return IntentDecision(
                intent_type="chat",
                confidence=0.88,
                reason="Small-talk acknowledgement detected",
                action="chat.respond",
                arguments={"text": normalized},
            )

        if lowered.endswith("?") or lowered.startswith(self._question_prefixes):
            return IntentDecision(
                intent_type="question",
                confidence=0.85,
                reason="Question form detected",
                action="answer.question",
                arguments={"text": normalized},
            )

        return IntentDecision(
            intent_type="goal",
            confidence=0.7,
            reason="No explicit command/question markers; fallback to deterministic pipeline",
            action="run.goal",
            arguments={"goal": normalized},
        )

    def _is_small_talk(self, lowered: str) -> bool:
        if lowered in self._chat_phrases:
            return True

        polite_prefixes = (
            "yes boss",
            "ok boss",
            "haan boss",
            "ji boss",
            "thanks boss",
        )
        if lowered in polite_prefixes:
            return True

        return False

    @staticmethod
    def _detect_command(lowered: str) -> tuple[str, dict[str, str], str] | None:
        if lowered in {"open chrome", "launch chrome", "start chrome"}:
            return "system.open_chrome", {}, "Explicit Chrome launch command"

        if lowered in {"open edge", "launch edge", "start edge"}:
            return "system.open_edge", {}, "Explicit Edge launch command"

        if lowered in {"open notepad", "launch notepad", "start notepad"}:
            return "system.open_notepad", {}, "Explicit Notepad launch command"

        if lowered in {"open mail", "open email", "launch mail"}:
            return "system.open_mail", {}, "Email client open command detected"

        mail_match = re.search(r"send\s+(?:an\s+)?email\s+to\s+(\S+)\s+about\s+(.+)", lowered, re.IGNORECASE)
        if mail_match:
            return (
                "system.compose_email",
                {
                    "to": mail_match.group(1),
                    "subject": mail_match.group(2).strip(),
                },
                "Email compose command detected",
            )

        for prefix in ("open this website ", "open website ", "open url "):
            if lowered.startswith(prefix):
                url = lowered[len(prefix) :].strip()
                return "system.open_website", {"url": url}, "Website open command detected"

        website_match = re.search(r"\b(https?://\S+|www\.\S+)\b", lowered)
        if website_match and lowered.startswith("open "):
            return "system.open_website", {"url": website_match.group(1)}, "URL in open command detected"

        folder_match = re.search(r"(?:create|make)\s+(?:a\s+)?folder\s+(?:named\s+)?(.+)", lowered, re.IGNORECASE)
        if folder_match:
            return (
                "system.create_folder",
                {"path": folder_match.group(1).strip().strip('"')},
                "Folder creation command detected",
            )

        download_match = re.search(r"download\s+(https?://\S+)(?:\s+to\s+(.+))?", lowered, re.IGNORECASE)
        if download_match:
            args = {"url": download_match.group(1).strip()}
            destination = download_match.group(2)
            if destination:
                args["path"] = destination.strip().strip('"')
            return "system.download", args, "Download command detected"

        return None


class ActionExecutorAgent:
    """Executes a bounded set of safe desktop actions."""

    def execute(self, decision: IntentDecision) -> ActionResult:
        action = decision.action or "unknown"
        try:
            if action == "system.open_chrome":
                self._launch_app("chrome")
                return ActionResult(action=action, success=True, executed=True, summary="Chrome opened", details=())

            if action == "system.open_edge":
                self._launch_app("msedge")
                return ActionResult(action=action, success=True, executed=True, summary="Edge opened", details=())

            if action == "system.open_notepad":
                self._launch_app("notepad")
                return ActionResult(action=action, success=True, executed=True, summary="Notepad opened", details=())

            if action == "system.open_mail":
                webbrowser.open("mailto:")
                return ActionResult(
                    action=action,
                    success=True,
                    executed=True,
                    summary="Opened default email client",
                    details=(),
                )

            if action == "system.open_website":
                raw_url = decision.arguments.get("url", "")
                url = _normalize_url(raw_url)
                if not url:
                    return ActionResult(
                        action=action,
                        success=False,
                        executed=False,
                        summary="Could not detect a valid website URL",
                        details=("Use: open website https://example.com",),
                    )
                webbrowser.open(url)
                return ActionResult(action=action, success=True, executed=True, summary=f"Opened {url}", details=())

            if action == "system.compose_email":
                to_email = decision.arguments.get("to", "")
                subject = decision.arguments.get("subject", "")
                mailto = f"mailto:{to_email}?subject={quote(subject)}"
                webbrowser.open(mailto)
                return ActionResult(
                    action=action,
                    success=True,
                    executed=True,
                    summary=f"Opened email compose for {to_email}",
                    details=("Message was NOT auto-sent. Review and send manually.",),
                )

            if action == "system.create_folder":
                raw_path = decision.arguments.get("path", "")
                target = _safe_folder_path(raw_path)
                if target is None:
                    return ActionResult(
                        action=action,
                        success=False,
                        executed=False,
                        summary="Folder path is invalid",
                        details=("Use: create folder <path>",),
                    )
                target.mkdir(parents=True, exist_ok=True)
                return ActionResult(
                    action=action,
                    success=True,
                    executed=True,
                    summary=f"Folder ready at {target}",
                    details=(),
                )

            if action == "system.download":
                url = _normalize_url(decision.arguments.get("url", ""))
                if not url:
                    return ActionResult(
                        action=action,
                        success=False,
                        executed=False,
                        summary="Download URL missing or invalid",
                        details=("Use: download https://example.com/file.zip to Downloads/file.zip",),
                    )
                destination = decision.arguments.get("path", "")
                target = _download_target(url=url, destination=destination)
                target.parent.mkdir(parents=True, exist_ok=True)
                urlretrieve(url, str(target))
                return ActionResult(
                    action=action,
                    success=True,
                    executed=True,
                    summary=f"Downloaded file to {target}",
                    details=(),
                )

            return ActionResult(
                action=action,
                success=False,
                executed=False,
                summary="Command not supported for direct desktop execution",
                details=("Falling back to deterministic task pipeline.",),
            )
        except Exception as exc:  # pragma: no cover - protective shell around OS actions
            return ActionResult(
                action=action,
                success=False,
                executed=False,
                summary=f"Action failed: {exc}",
                details=(type(exc).__name__,),
            )

    @staticmethod
    def _launch_app(binary: str) -> None:
        if os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class OutcomeAuditorAgent:
    """Produces human-readable outcome summaries for the operator/admin."""

    def build_report(
        self,
        *,
        decision: IntentDecision,
        language: str,
        action_result: ActionResult | None = None,
        question_answer: str | None = None,
        pipeline_payload: dict[str, object] | None = None,
    ) -> str:
        resolved = "hi" if str(language).lower().startswith("hi") else "en"

        if decision.intent_type == "question":
            answer = _normalize(question_answer or "")
            if resolved == "hi":
                return f"Intent: question | Outcome: direct answer delivered.\nAnswer: {answer}"
            return f"Intent: question | Outcome: direct answer delivered.\nAnswer: {answer}"

        if decision.intent_type == "memory":
            if resolved == "hi":
                return "Intent: memory | Outcome: your preference/note has been saved."
            return "Intent: memory | Outcome: your preference/note has been saved."

        if decision.intent_type == "chat":
            answer = _normalize(question_answer or "")
            return f"Intent: chat | Outcome: conversational response delivered.\nAnswer: {answer}"

        if action_result is not None:
            detail_text = " | ".join(action_result.details) if action_result.details else "none"
            status = "success" if action_result.success else "failed"
            return (
                f"Intent: command | Action: {action_result.action} | Status: {status}\n"
                f"Outcome: {action_result.summary}\n"
                f"Findings: {detail_text}"
            )

        if pipeline_payload is not None:
            status = pipeline_payload.get("status", "unknown")
            run_id = pipeline_payload.get("run_id", "n/a")
            plan_id = pipeline_payload.get("plan_id", "n/a")
            return (
                f"Intent: goal | Pipeline status: {status}\n"
                f"Run: {run_id} | Plan: {plan_id}\n"
                "Outcome: deterministic orchestration executed."
            )

        return "Intent recognized but no outcome details available."


def _normalize(value: str) -> str:
    return " ".join(str(value).split())


def _normalize_url(url: str) -> str:
    normalized = _normalize(url)
    if not normalized:
        return ""
    if normalized.startswith("www."):
        return f"https://{normalized}"
    if not normalized.startswith(("http://", "https://", "mailto:")):
        return f"https://{normalized}"
    return normalized


def _safe_folder_path(raw_path: str) -> Path | None:
    normalized = _normalize(raw_path)
    if not normalized:
        return None

    candidate = Path(normalized).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate

    return candidate


def _download_target(*, url: str, destination: str) -> Path:
    if destination:
        path = Path(destination).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    filename = Path(url.split("?", 1)[0]).name or "download.bin"
    return Path.home() / "Downloads" / filename


__all__ = [
    "IntentDecision",
    "ActionResult",
    "IntentPlannerAgent",
    "ActionExecutorAgent",
    "OutcomeAuditorAgent",
]
