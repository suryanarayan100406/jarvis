"""Tests for assistant personalization, startup briefs, and Ollama fallback."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from runtime.assistant.personal_assistant import (
    OllamaResponseEngine,
    StartupBriefingService,
    compose_question_answer,
    detect_current_city,
    compose_reply,
    compose_social_reply,
    normalize_language,
)


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeTextResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> bytes:
        return self._text.encode("utf-8")

    def __enter__(self) -> _FakeTextResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class PersonalAssistantTests(unittest.TestCase):
    def test_normalize_language_defaults_to_hindi(self) -> None:
        self.assertEqual(normalize_language(None), "hi")
        self.assertEqual(normalize_language(""), "hi")
        self.assertEqual(normalize_language("en-US"), "en")

    def test_compose_reply_supports_hindi_style(self) -> None:
        text = compose_reply(
            user_text="collect diagnostics",
            payload={"status": "completed", "validation_passed": True},
            actor_id="boss",
            language="hi",
        )

        self.assertIn("Ho gaya", text)
        self.assertIn("boss", text)

    def test_compose_social_reply_handles_ji_ack(self) -> None:
        text = compose_social_reply(user_text="ji", actor_id="boss", language="hi")

        self.assertIn("Ji boss", text)
    def test_compose_question_answer_returns_direct_answer(self) -> None:
        answer = compose_question_answer(
            user_text="Who are you?",
            actor_id="boss",
            language="hi",
        )

        self.assertIn("FRIDAY", answer)

    def test_compose_question_answer_handles_name_question_variant(self) -> None:
        answer = compose_question_answer(
            user_text="your name?",
            actor_id="boss",
            language="en",
        )

        self.assertIn("I am FRIDAY", answer)

    def test_compose_question_answer_handles_simple_math(self) -> None:
        answer = compose_question_answer(
            user_text="2+2?",
            actor_id="boss",
            language="en",
        )

        self.assertIn("4", answer)

    def test_startup_brief_live_uses_weather_and_headlines(self) -> None:
        def fake_json_fetcher(url: str, timeout: int) -> dict[str, object]:
            return {
                "current_condition": [
                    {
                        "temp_C": "29",
                        "weatherDesc": [{"value": "Partly cloudy"}],
                    }
                ]
            }

        def fake_text_fetcher(url: str, timeout: int) -> str:
            return """<?xml version='1.0' encoding='UTF-8'?>
<rss><channel>
<item><title>Headline one</title></item>
<item><title>Headline two</title></item>
</channel></rss>"""

        service = StartupBriefingService(
            json_fetcher=fake_json_fetcher,
            text_fetcher=fake_text_fetcher,
        )

        briefing = service.build_briefing(
            actor_id="boss",
            language="hi",
            city="Bengaluru",
            news_topic="India technology",
            live=True,
        )

        self.assertIn("Bengaluru ka mausam", briefing)
        self.assertIn("Headline one", briefing)
        self.assertIn("Headline two", briefing)

    def test_startup_brief_falls_back_when_live_disabled(self) -> None:
        service = StartupBriefingService()

        briefing = service.build_briefing(
            actor_id="boss",
            language="hi",
            city="Bengaluru",
            news_topic="India technology",
            live=False,
        )

        self.assertIn("Live weather/news", briefing)

    def test_build_greeting_omits_briefing_segments(self) -> None:
        service = StartupBriefingService()

        greeting = service.build_greeting(actor_id="boss", language="hi")

        self.assertIn("FRIDAY online", greeting)
        self.assertNotIn("weather/news", greeting)

    def test_ollama_response_engine_returns_generated_text(self) -> None:
        engine = OllamaResponseEngine(
            enabled=True,
            host="http://127.0.0.1:11434",
            model="llama3.2:3b",
            timeout_seconds=2,
        )

        with patch(
            "runtime.assistant.personal_assistant.urlopen",
            return_value=_FakeHttpResponse({"response": "Done boss, all set."}),
        ):
            response = engine.generate_reply(
                user_text="collect diagnostics",
                actor_id="boss",
                language="hi",
                payload={"status": "completed", "validation_passed": True, "summary": "ok"},
            )

        self.assertEqual(response, "Done boss, all set.")

    def test_ollama_response_engine_falls_back_to_chat_when_generate_empty(self) -> None:
        engine = OllamaResponseEngine(
            enabled=True,
            host="http://127.0.0.1:11434",
            model="gemma4:latest",
            timeout_seconds=2,
        )

        with patch(
            "runtime.assistant.personal_assistant.urlopen",
            side_effect=[
                _FakeHttpResponse({"response": ""}),
                _FakeHttpResponse({"message": {"content": "Chat fallback response"}}),
            ],
        ):
            response = engine.generate_reply(
                user_text="who are you",
                actor_id="boss",
                language="hi",
                payload={"status": "completed", "validation_passed": True, "summary": "ok"},
            )

        self.assertEqual(response, "Chat fallback response")

    def test_ollama_response_engine_uses_compact_retry_when_chat_is_empty(self) -> None:
        engine = OllamaResponseEngine(
            enabled=True,
            host="http://127.0.0.1:11434",
            model="gemma4:latest",
            timeout_seconds=2,
        )

        with patch(
            "runtime.assistant.personal_assistant.urlopen",
            side_effect=[
                _FakeHttpResponse({"response": ""}),
                _FakeHttpResponse({"message": {"content": ""}}),
                _FakeHttpResponse({"message": {"content": "Compact fallback response"}}),
            ],
        ):
            response = engine.generate_reply(
                user_text="who are you",
                actor_id="boss",
                language="hi",
                payload={"status": "completed", "validation_passed": True, "summary": "ok"},
            )

        self.assertEqual(response, "Compact fallback response")

    def test_ollama_response_engine_recovers_after_generate_error(self) -> None:
        engine = OllamaResponseEngine(
            enabled=True,
            host="http://127.0.0.1:11434",
            model="gemma4:latest",
            timeout_seconds=2,
        )

        with patch(
            "runtime.assistant.personal_assistant.urlopen",
            side_effect=[
                URLError("offline"),
                _FakeHttpResponse({"message": {"content": "Recovered via chat"}}),
            ],
        ):
            response = engine.generate_reply(
                user_text="who are you",
                actor_id="boss",
                language="hi",
                payload={"status": "completed", "validation_passed": True, "summary": "ok"},
            )

        self.assertEqual(response, "Recovered via chat")

    def test_ollama_response_engine_disables_after_connection_error(self) -> None:
        engine = OllamaResponseEngine(
            enabled=True,
            host="http://127.0.0.1:11434",
            model="llama3.2:3b",
            timeout_seconds=2,
        )

        with patch("runtime.assistant.personal_assistant.urlopen", side_effect=URLError("offline")):
            first = engine.generate_reply(
                user_text="collect diagnostics",
                actor_id="boss",
                language="hi",
                payload={"status": "completed", "validation_passed": True, "summary": "ok"},
            )

        second = engine.generate_reply(
            user_text="collect diagnostics",
            actor_id="boss",
            language="hi",
            payload={"status": "completed", "validation_passed": True, "summary": "ok"},
        )

        self.assertIsNone(first)
        self.assertIsNone(second)

    def test_detect_current_city_from_geo_api(self) -> None:
        with patch(
            "runtime.assistant.personal_assistant.urlopen",
            return_value=_FakeTextResponse('{"city": "Pune"}'),
        ):
            city = detect_current_city()

        self.assertEqual(city, "Pune")


if __name__ == "__main__":
    unittest.main()
