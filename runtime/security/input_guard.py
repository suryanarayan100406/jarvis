"""Baseline prompt-injection and identity-override protection filters."""

from __future__ import annotations

import re
from dataclasses import dataclass


IDENTITY_OVERRIDE_MESSAGE = "Attempted identity override detected. Ignoring."


@dataclass(frozen=True)
class SecurityFilterDecision:
    """Result of analyzing input for security and identity threats."""

    blocked: bool
    flags: list[str]
    reason: str
    sanitized_text: str
    isolation_gate: str


class PromptSecurityFilter:
    """Detects baseline prompt-injection and identity-override patterns."""

    _identity_override_patterns = [
        re.compile(r"\b(ignore|forget|override)\b.{0,80}\b(identity|directive|instructions|persona)\b", re.IGNORECASE),
        re.compile(r"\byou are now\b.{0,50}\b(another|different|new|jarvis|assistant)?\b", re.IGNORECASE),
        re.compile(r"\b(not friday|change your identity|override identity)\b", re.IGNORECASE),
    ]

    _prompt_injection_patterns = [
        re.compile(r"\bignore previous instructions\b", re.IGNORECASE),
        re.compile(r"\bdisregard (all|any) (prior|previous)\b", re.IGNORECASE),
        re.compile(r"\b(reveal|print|show)\b.{0,60}\b(system prompt|hidden prompt|developer prompt)\b", re.IGNORECASE),
        re.compile(r"\bact as\b.{0,30}\b(system|developer|root)\b", re.IGNORECASE),
    ]

    _embedded_instruction_patterns = [
        re.compile(r"\b(run|execute|invoke|launch)\b.{0,40}\b(command|script|payload)\b", re.IGNORECASE),
        re.compile(r"\b(powershell|bash|cmd|sudo|rm -rf|format c:)\b", re.IGNORECASE),
    ]

    _untrusted_sources = {"document", "web", "email", "attachment", "external"}
    _trusted_sources = {"user", "operator", "system", "memory", "session"}

    _tool_execution_patterns = [
        re.compile(r"\b(run|execute|launch|invoke)\b.{0,40}\b(tool|command|script|shell|terminal)\b", re.IGNORECASE),
        re.compile(r"\b(use|call)\b.{0,20}\b(tool|plugin|function)\b", re.IGNORECASE),
    ]

    def analyze(self, text: str, source: str = "user", explicit_authorization: bool = False) -> SecurityFilterDecision:
        """Analyze content and return block/allow decision with threat flags."""
        if not isinstance(text, str):
            raise TypeError("Input text must be a string")

        normalized_source = " ".join(str(source).split()).lower() or "user"
        flags: list[str] = []
        isolation_gate = "trusted_context"

        if normalized_source in self._untrusted_sources:
            isolation_gate = "untrusted_context"
        elif normalized_source not in self._trusted_sources:
            isolation_gate = "unknown_context"
            flags.append("unknown_source_context")

        if self._matches_any(text, self._identity_override_patterns):
            flags.append("identity_override_attempt")

        if self._matches_any(text, self._prompt_injection_patterns):
            flags.append("prompt_injection_attempt")

        if normalized_source in self._untrusted_sources and self._matches_any(text, self._embedded_instruction_patterns):
            flags.append("untrusted_embedded_instruction")

        if normalized_source in self._untrusted_sources and self._matches_any(text, self._tool_execution_patterns):
            flags.append("untrusted_tool_execution_request")

        blocked = False
        reason = "Input accepted"

        if "identity_override_attempt" in flags:
            blocked = True
            reason = IDENTITY_OVERRIDE_MESSAGE
        elif "prompt_injection_attempt" in flags:
            blocked = True
            reason = "Potential prompt injection detected. Ignoring unsafe instructions."
        elif "untrusted_tool_execution_request" in flags and not explicit_authorization:
            blocked = True
            reason = "Untrusted context cannot directly request tool execution without explicit authorization."
        elif "untrusted_embedded_instruction" in flags and not explicit_authorization:
            blocked = True
            reason = "Untrusted embedded instruction requires explicit authorization."
        elif "unknown_source_context" in flags and self._matches_any(text, self._embedded_instruction_patterns):
            blocked = True
            reason = "Unknown source context with executable instructions requires explicit trust classification."

        sanitized = text if not blocked else "[FILTERED_UNSAFE_INPUT]"
        return SecurityFilterDecision(
            blocked=blocked,
            flags=flags,
            reason=reason,
            sanitized_text=sanitized,
            isolation_gate=isolation_gate,
        )

    def _matches_any(self, text: str, patterns: list[re.Pattern[str]]) -> bool:
        return any(pattern.search(text) is not None for pattern in patterns)
