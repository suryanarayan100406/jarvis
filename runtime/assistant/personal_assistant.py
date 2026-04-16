"""Utilities for conversational assistant behavior in CLI mode."""

from __future__ import annotations

import json
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
        part_of_day = _part_of_day(now)
        greeting = _greeting(actor_id=actor_id, language=resolved_language, part_of_day=part_of_day)

        if not live:
            return _offline_brief(greeting=greeting, language=resolved_language)

        weather = self._weather_line(city=city, language=resolved_language)
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

        body = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.35,
                    "num_predict": 180,
                },
            }
        ).encode("utf-8")

        request = Request(
            f"{self.host}/api/generate",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "FRIDAY/1.0",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_text = response.read().decode("utf-8")
                payload_json = json.loads(response_text)
        except (URLError, TimeoutError, json.JSONDecodeError):
            self._disabled_by_error = True
            return None

        content = _normalize_text(str(payload_json.get("response", "")))
        if not content:
            return None
        return content


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
    "StartupBriefingService",
    "OllamaResponseEngine",
]
