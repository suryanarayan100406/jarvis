"""Utilities for conversational assistant behavior in CLI mode."""

from __future__ import annotations

import ast
import json
import operator
import re
from datetime import datetime
from typing import Callable
from urllib.error import URLError
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen
from xml.etree import ElementTree

JsonFetcher = Callable[[str, int], dict[str, object]]
TextFetcher = Callable[[str, int], str]


def normalize_language(language: str | None) -> str:
    """Normalize language values to supported two-letter identifiers."""
    normalized = str(language or "").strip().lower()
    if normalized.startswith("en"):
        return "en"
    return "hi"


def compose_reply(
    *,
    user_text: str,
    payload: dict[str, object],
    actor_id: str,
    language: str,
) -> str:
    """Render deterministic assistant responses with language-aware phrasing."""
    status = str(payload.get("status", "")).strip().lower()
    validation_passed = bool(payload.get("validation_passed", False))
    resolved_language = normalize_language(language)

    if resolved_language == "hi":
        if status == "completed" and validation_passed:
            return f"Ho gaya, {actor_id}. Maine complete kar diya: {user_text}."
        if status == "completed":
            return (
                f"{actor_id}, run complete hua hai, lekin validation mein kuch issues aaye hain. "
                "Agar chaho to main details nikal deta hoon."
            )
        return f"{actor_id}, run status {status} ke saath khatam hua."

    if status == "completed" and validation_passed:
        return f"Done, {actor_id}. I completed: {user_text}."
    if status == "completed":
        return f"{actor_id}, the run completed but validation reported issues."
    return f"{actor_id}, the run ended with status {status}."


def compose_question_answer(
    *,
    user_text: str,
    actor_id: str,
    language: str,
) -> str:
    """Provide lightweight direct answers for common question categories."""
    resolved_language = normalize_language(language)
    lowered = _normalize_text(user_text).lower()

    math_value = _evaluate_simple_math(lowered)
    if math_value is not None:
        if resolved_language == "hi":
            return f"{actor_id}, iska answer {math_value} hai."
        return f"{actor_id}, the answer is {math_value}."

    if _looks_like_name_query(lowered):
        if resolved_language == "hi":
            return f"Main FRIDAY hoon, {actor_id}. Main tumhara local-first AI assistant hoon."
        return f"I am FRIDAY, {actor_id}, your local-first AI assistant."

    if "weather" in lowered:
        if resolved_language == "hi":
            return (
                f"{actor_id}, latest weather startup briefing ke time pe fetch hota hai. "
                "Agar chaho to abhi bhi main weather refresh command se pull kar sakta hoon."
            )
        return (
            f"{actor_id}, weather is fetched during startup briefing. "
            "If you want, I can also refresh it now on command."
        )

    if "news" in lowered:
        if resolved_language == "hi":
            return f"{actor_id}, main trusted RSS sources se headlines summarize karta hoon."
        return f"{actor_id}, I summarize headlines from trusted RSS sources."

    if "who are you" in lowered or "tum kaun" in lowered:
        if resolved_language == "hi":
            return f"Main FRIDAY hoon, {actor_id}. Main tumhara local-first AI assistant hoon."
        return f"I am FRIDAY, {actor_id}, your local-first AI assistant."

    if resolved_language == "hi":
        return (
            f"{actor_id}, mujhe samajh aaya. Iska short answer: haan, main help kar sakta hoon. "
            "Agar chahe to main isko step-by-step tod kar bhi bata doon."
        )
    return (
        f"{actor_id}, understood. Short answer: yes, I can help with that. "
        "I can also break it down step by step if you want."
    )


def _looks_like_name_query(lowered: str) -> bool:
    signals = (
        "who are you",
        "your name",
        "what is your name",
        "what's your name",
        "tum kaun",
        "tumhara naam",
        "aapka naam",
        "naam kya hai",
    )
    return any(signal in lowered for signal in signals)


def _evaluate_simple_math(lowered: str) -> int | float | None:
    candidate = lowered.strip().rstrip("?")
    candidate = candidate.replace("x", "*")

    # Keep the evaluator intentionally strict to avoid executing arbitrary expressions.
    if not candidate or not re.fullmatch(r"[0-9\s\+\-\*\/\(\)\.]+", candidate):
        return None

    try:
        expression = ast.parse(candidate, mode="eval")
        value = _eval_node(expression.body)
    except Exception:
        return None

    if isinstance(value, float) and value.is_integer():
        return int(value)
    return round(float(value), 6)


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op_type = type(node.op)
        if op_type not in _ALLOWED_BINARY_OPS:
            raise ValueError("unsupported operator")
        return _ALLOWED_BINARY_OPS[op_type](left, right)

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        op_type = type(node.op)
        if op_type not in _ALLOWED_UNARY_OPS:
            raise ValueError("unsupported unary operator")
        return _ALLOWED_UNARY_OPS[op_type](operand)

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)

    raise ValueError("unsupported expression")


_ALLOWED_BINARY_OPS: dict[type[ast.AST], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_ALLOWED_UNARY_OPS: dict[type[ast.AST], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def compose_social_reply(*, user_text: str, actor_id: str, language: str) -> str:
    """Return short conversational replies for acknowledgements and small-talk."""
    resolved_language = normalize_language(language)
    lowered = _normalize_text(user_text).lower()

    if resolved_language == "hi":
        if lowered in {"thanks", "thank you", "shukriya"}:
            return f"Always, {actor_id}. Aur kuch karein?"
        if lowered in {"ji", "haan", "han", "ok", "okay", "theek", "thik"}:
            return f"Ji {actor_id}, boliye. Main sun raha hoon."
        return f"Haan {actor_id}, main yahin hoon. Aap bolo."

    if lowered in {"thanks", "thank you"}:
        return f"Always, {actor_id}. What should we do next?"
    if lowered in {"yes", "ok", "okay", "yeah"}:
        return f"Yes {actor_id}, I am here. Tell me what you need."
    return f"I am with you, {actor_id}. Go ahead."


def detect_current_city(timeout_seconds: int = 2) -> str | None:
    """Resolve approximate city using public IP geolocation with graceful fallback."""
    request = Request(
        "https://ipapi.co/json/",
        headers={"User-Agent": "FRIDAY/1.0", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=max(1, int(timeout_seconds))) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
            city = _normalize_text(str(payload.get("city", "")))
            return city or None
    except Exception:
        return None


class StartupBriefingService:
    """Builds startup weather/news briefings with graceful offline fallback."""

    def __init__(
        self,
        *,
        json_fetcher: JsonFetcher | None = None,
        text_fetcher: TextFetcher | None = None,
    ) -> None:
        self._json_fetcher = json_fetcher or _fetch_json
        self._text_fetcher = text_fetcher or _fetch_text

    def build_briefing(
        self,
        *,
        actor_id: str,
        language: str,
        city: str,
        news_topic: str,
        live: bool,
        now: datetime | None = None,
    ) -> str:
        """Return a single-line startup briefing tuned to language and time of day."""
        resolved_language = normalize_language(language)
        resolved_city = _normalize_text(city)
        if not resolved_city:
            resolved_city = detect_current_city() or "Bengaluru"
        part_of_day = _part_of_day(now)
        greeting = _greeting(actor_id=actor_id, language=resolved_language, part_of_day=part_of_day)

        if not live:
            return _offline_brief(greeting=greeting, language=resolved_language)

        weather = self._weather_line(city=resolved_city, language=resolved_language)
        headlines = self._headline_line(topic=news_topic, language=resolved_language)

        if weather is None and headlines is None:
            return _offline_brief(greeting=greeting, language=resolved_language)

        segments = [greeting]
        if weather:
            segments.append(weather)
        if headlines:
            segments.append(headlines)
        return " ".join(segments)

    def build_greeting(
        self,
        *,
        actor_id: str,
        language: str,
        now: datetime | None = None,
    ) -> str:
        """Return greeting without weather/news briefing segments."""
        resolved_language = normalize_language(language)
        part_of_day = _part_of_day(now)
        return _greeting(actor_id=actor_id, language=resolved_language, part_of_day=part_of_day)

    def _weather_line(self, *, city: str, language: str) -> str | None:
        normalized_city = " ".join(str(city).split()) or "Bengaluru"
        url = f"https://wttr.in/{quote(normalized_city)}?format=j1"
        try:
            payload = self._json_fetcher(url, 2)
        except Exception:
            return None

        try:
            current = payload["current_condition"][0]  # type: ignore[index]
            temp_c = str(current.get("temp_C", "?")).strip()
            desc = str(current["weatherDesc"][0]["value"]).strip()  # type: ignore[index]
        except Exception:
            return None

        if language == "hi":
            return f"{normalized_city} ka mausam {temp_c} degree Celsius hai, {desc}."
        return f"Weather in {normalized_city} is {temp_c} C, {desc}."

    def _headline_line(self, *, topic: str, language: str) -> str | None:
        normalized_topic = " ".join(str(topic).split()) or "India technology"
        url = (
            "https://news.google.com/rss/search"
            f"?q={quote_plus(normalized_topic)}&hl=en-IN&gl=IN&ceid=IN:en"
        )
        try:
            xml_text = self._text_fetcher(url, 2)
            root = ElementTree.fromstring(xml_text)
        except Exception:
            return None

        titles: list[str] = []
        for title_node in root.findall("./channel/item/title"):
            title = _normalize_text(title_node.text or "")
            if not title:
                continue
            titles.append(title)
            if len(titles) >= 2:
                break

        if not titles:
            return None

        if language == "hi":
            return "Aaj ki top headlines: " + " | ".join(titles)
        return "Top headlines: " + " | ".join(titles)


class OllamaResponseEngine:
    """Small helper for optional local-model responses via Ollama."""

    def __init__(
        self,
        *,
        enabled: bool,
        host: str,
        model: str,
        timeout_seconds: int,
    ) -> None:
        self.enabled = bool(enabled)
        self.host = str(host).strip().rstrip("/") or "http://127.0.0.1:11434"
        self.model = str(model).strip() or "llama3.2:3b"
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._disabled_by_error = False

    def generate_reply(
        self,
        *,
        user_text: str,
        actor_id: str,
        language: str,
        payload: dict[str, object],
    ) -> str | None:
        if not self.enabled or self._disabled_by_error:
            return None

        prompt = _build_ollama_prompt(
            user_text=user_text,
            actor_id=actor_id,
            language=normalize_language(language),
            payload=payload,
        )

        options = {
            "temperature": 0.35,
            "num_predict": 180,
        }

        error_count = 0

        payload_json = self._safe_call_ollama(
            path="/api/generate",
            body={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": options,
            },
        )
        if payload_json is not None:
            content = _normalize_text(str(payload_json.get("response", "")))
            if content:
                return content
        else:
            error_count += 1

        chat_payload = self._safe_call_ollama(
            path="/api/chat",
            body={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": options,
            },
        )
        if chat_payload is not None:
            message = chat_payload.get("message", {})
            if isinstance(message, dict):
                chat_content = _normalize_text(str(message.get("content", "")))
                if chat_content:
                    return chat_content
        else:
            error_count += 1

        compact_prompt = _build_compact_ollama_prompt(
            user_text=user_text,
            actor_id=actor_id,
            language=normalize_language(language),
        )
        compact_chat_payload = self._safe_call_ollama(
            path="/api/chat",
            body={
                "model": self.model,
                "messages": [{"role": "user", "content": compact_prompt}],
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 512,
                },
            },
        )
        if compact_chat_payload is not None:
            compact_message = compact_chat_payload.get("message", {})
            if isinstance(compact_message, dict):
                compact_content = _normalize_text(str(compact_message.get("content", "")))
                if compact_content:
                    return compact_content

        if compact_chat_payload is None:
            error_count += 1

        if error_count >= 3:
            self._disabled_by_error = True
        return None

    def _safe_call_ollama(self, *, path: str, body: dict[str, object]) -> dict[str, object] | None:
        try:
            return self._call_ollama(path=path, body=body)
        except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
            return None

    def _call_ollama(self, *, path: str, body: dict[str, object]) -> dict[str, object]:
        encoded = json.dumps(body).encode("utf-8")
        request = Request(
            f"{self.host}{path}",
            data=encoded,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "FRIDAY/1.0",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            response_text = response.read().decode("utf-8")
            payload_json = json.loads(response_text)
        if not isinstance(payload_json, dict):
            raise ValueError("Unexpected Ollama response payload")
        return payload_json


def _build_ollama_prompt(
    *,
    user_text: str,
    actor_id: str,
    language: str,
    payload: dict[str, object],
) -> str:
    summary = _normalize_text(str(payload.get("summary", "")))
    status = _normalize_text(str(payload.get("status", "")))
    validation = "pass" if bool(payload.get("validation_passed", False)) else "issues"

    if language == "hi":
        style = (
            "Tum FRIDAY ho. Friendly, crisp, practical jawab do. "
            "Hindi ya Hinglish mein 1-3 lines mein respond karo. "
            "Internal IDs ya metadata tab tak mat batao jab tak user specifically na maange."
        )
    else:
        style = (
            "You are FRIDAY. Reply in a friendly, concise, practical tone in 1-3 lines. "
            "Do not expose internal run IDs or metadata unless explicitly asked."
        )

    return "\n".join(
        [
            style,
            f"Actor: {actor_id}",
            f"User request: {user_text}",
            f"Pipeline status: {status}",
            f"Validation: {validation}",
            f"Pipeline summary: {summary}",
            "Now produce the assistant response only.",
        ]
    )


def _build_compact_ollama_prompt(*, user_text: str, actor_id: str, language: str) -> str:
    if language == "hi":
        return (
            f"Tum FRIDAY ho. {actor_id} ko ek short helpful jawab do (1-2 lines). "
            f"User request: {user_text}"
        )
    return (
        f"You are FRIDAY. Give {actor_id} a short helpful answer (1-2 lines). "
        f"User request: {user_text}"
    )


def _greeting(*, actor_id: str, language: str, part_of_day: str) -> str:
    if language == "hi":
        if part_of_day == "morning":
            return f"Shubh prabhat {actor_id}. Main FRIDAY online hoon."
        if part_of_day == "evening":
            return f"Shubh sandhya {actor_id}. Main FRIDAY online hoon."
        return f"Namaste {actor_id}. Main FRIDAY online hoon."

    if part_of_day == "morning":
        return f"Good morning {actor_id}. FRIDAY is online."
    if part_of_day == "evening":
        return f"Good evening {actor_id}. FRIDAY is online."
    return f"Hello {actor_id}. FRIDAY is online."


def _offline_brief(*, greeting: str, language: str) -> str:
    if language == "hi":
        return f"{greeting} Live weather/news abhi available nahi hai, par main ready hoon."
    return f"{greeting} Live weather/news is unavailable right now, but I am ready."


def _part_of_day(now: datetime | None) -> str:
    current = now or datetime.now()
    hour = current.hour
    if 5 <= hour < 12:
        return "morning"
    if hour >= 17 or hour < 5:
        return "evening"
    return "day"


def _fetch_json(url: str, timeout_seconds: int) -> dict[str, object]:
    text = _fetch_text(url, timeout_seconds)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Expected top-level JSON object")
    return payload


def _fetch_text(url: str, timeout_seconds: int) -> str:
    request = Request(url, headers={"User-Agent": "FRIDAY/1.0"}, method="GET")
    with urlopen(request, timeout=max(1, int(timeout_seconds))) as response:
        return response.read().decode("utf-8", errors="replace")


def _normalize_text(text: str) -> str:
    return " ".join(str(text).split())


__all__ = [
    "normalize_language",
    "compose_reply",
    "compose_question_answer",
    "compose_social_reply",
    "detect_current_city",
    "StartupBriefingService",
    "OllamaResponseEngine",
]
