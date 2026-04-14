"""Social-engineering signal detection for conversation flows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["low", "medium", "high", "critical"]


@dataclass(frozen=True)
class ConversationFlowTurn:
    speaker: str
    text: str
    source: str = "user"


@dataclass(frozen=True)
class SocialEngineeringSignal:
    signal: str
    score: float
    occurrences: int
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class SocialEngineeringAssessment:
    risk_score: float
    risk_level: RiskLevel
    should_flag: bool
    summary: str
    signals: tuple[SocialEngineeringSignal, ...]


class SocialEngineeringSignalDetector:
    """Detects social-engineering patterns in single-turn and multi-turn conversations."""

    _signal_patterns: dict[str, tuple[re.Pattern[str], ...]] = {
        "authority_impersonation": (
            re.compile(r"\b(i am|this is)\b.{0,25}\b(admin|administrator|security|it|ceo|manager|root)\b", re.IGNORECASE),
            re.compile(r"\bfrom (security|it|compliance|admin) team\b", re.IGNORECASE),
        ),
        "urgency_pressure": (
            re.compile(r"\b(urgent|immediately|right now|asap|no time|before it is too late)\b", re.IGNORECASE),
            re.compile(r"\bdo it now\b", re.IGNORECASE),
        ),
        "secrecy_isolation": (
            re.compile(r"\b(do not|don't) tell\b", re.IGNORECASE),
            re.compile(r"\bkeep (this )?(secret|confidential)\b", re.IGNORECASE),
            re.compile(r"\bwithout (logging|audit|approval)\b", re.IGNORECASE),
        ),
        "credential_harvest": (
            re.compile(r"\b(password|api key|access key|token|secret|2fa|one-time code|ssh key)\b", re.IGNORECASE),
            re.compile(r"\b(send|share|reveal|paste)\b.{0,30}\b(password|token|key|secret|credential)\b", re.IGNORECASE),
        ),
        "policy_bypass_request": (
            re.compile(r"\b(ignore|bypass|disable|skip)\b.{0,40}\b(policy|approval|security|guardrail|safety)\b", re.IGNORECASE),
            re.compile(r"\b(run it anyway|override controls)\b", re.IGNORECASE),
        ),
        "off_channel_redirect": (
            re.compile(r"\b(move|switch)\b.{0,20}\b(private|direct|dm|email|text)\b", re.IGNORECASE),
            re.compile(r"\bcontact me (privately|off[- ]channel)\b", re.IGNORECASE),
        ),
    }

    _weights: dict[str, float] = {
        "authority_impersonation": 0.24,
        "urgency_pressure": 0.14,
        "secrecy_isolation": 0.20,
        "credential_harvest": 0.35,
        "policy_bypass_request": 0.30,
        "off_channel_redirect": 0.16,
        "persistent_pressure": 0.16,
        "coercive_combo": 0.22,
    }

    _ignored_speakers = {"assistant", "system"}

    def analyze_text(self, text: str, *, speaker: str = "user", source: str = "user") -> SocialEngineeringAssessment:
        """Analyze a single message for social-engineering signals."""
        turn = ConversationFlowTurn(speaker=speaker, source=source, text=text)
        return self.analyze_flow((turn,))

    def analyze_flow(
        self,
        turns: list[ConversationFlowTurn] | tuple[ConversationFlowTurn, ...],
    ) -> SocialEngineeringAssessment:
        """Analyze a conversation flow and return signal-level and aggregate risk output."""
        normalized_turns = [
            ConversationFlowTurn(
                speaker=_normalize_required(turn.speaker, "speaker").lower(),
                source=_normalize_required(turn.source, "source").lower(),
                text=_normalize_required(turn.text, "text"),
            )
            for turn in turns
        ]
        if not normalized_turns:
            raise ValueError("turns must include at least one item")

        evidence_map: dict[str, list[str]] = {key: [] for key in self._signal_patterns}
        count_map: dict[str, int] = {key: 0 for key in self._signal_patterns}
        urgency_turn_hits = 0

        for turn in normalized_turns:
            if turn.speaker in self._ignored_speakers:
                continue

            turn_matches: set[str] = set()
            compact_text = _compact_text(turn.text)
            for signal, patterns in self._signal_patterns.items():
                if any(pattern.search(compact_text) for pattern in patterns):
                    turn_matches.add(signal)
                    count_map[signal] += 1
                    if len(evidence_map[signal]) < 3:
                        evidence_map[signal].append(_excerpt(compact_text))

            if "urgency_pressure" in turn_matches:
                urgency_turn_hits += 1

            if (
                "authority_impersonation" in turn_matches
                and ("secrecy_isolation" in turn_matches or "credential_harvest" in turn_matches)
            ):
                count_map["coercive_combo"] = count_map.get("coercive_combo", 0) + 1
                evidence = "authority + secrecy/credential pattern in same turn"
                if len(evidence_map.setdefault("coercive_combo", [])) < 3:
                    evidence_map["coercive_combo"].append(evidence)

        if urgency_turn_hits >= 2:
            count_map["persistent_pressure"] = urgency_turn_hits
            evidence_map["persistent_pressure"] = [f"urgency detected across {urgency_turn_hits} turns"]

        signals = self._build_signals(count_map, evidence_map)
        risk_score = min(1.0, sum(signal.score for signal in signals))
        risk_level = _risk_level_for_score(risk_score)

        primary_signals = sorted(signals, key=lambda item: item.score, reverse=True)
        has_secret_pressure_combo = any(signal.signal == "credential_harvest" for signal in signals) and any(
            signal.signal in {"authority_impersonation", "urgency_pressure", "secrecy_isolation"} for signal in signals
        )
        should_flag = risk_level in {"high", "critical"} or has_secret_pressure_combo

        if not signals:
            summary = "No social-engineering signals detected."
        else:
            top = ", ".join(signal.signal for signal in primary_signals[:3])
            summary = f"Detected {len(signals)} social-engineering signals: {top}."

        return SocialEngineeringAssessment(
            risk_score=round(risk_score, 4),
            risk_level=risk_level,
            should_flag=should_flag,
            summary=summary,
            signals=tuple(primary_signals),
        )

    def _build_signals(
        self,
        count_map: dict[str, int],
        evidence_map: dict[str, list[str]],
    ) -> list[SocialEngineeringSignal]:
        signals: list[SocialEngineeringSignal] = []
        for signal, count in count_map.items():
            if count < 1:
                continue

            weight = self._weights.get(signal, 0.1)
            scaled = min(0.4, weight + ((min(count, 3) - 1) * 0.05))
            evidence = tuple(evidence_map.get(signal, []))
            signals.append(
                SocialEngineeringSignal(
                    signal=signal,
                    score=round(scaled, 4),
                    occurrences=count,
                    evidence=evidence,
                )
            )
        return signals


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _compact_text(value: str) -> str:
    return " ".join(value.split())


def _excerpt(value: str, *, max_chars: int = 120) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[: max_chars - 3]}..."


def _risk_level_for_score(score: float) -> RiskLevel:
    if score >= 0.75:
        return "critical"
    if score >= 0.5:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"


__all__ = [
    "ConversationFlowTurn",
    "SocialEngineeringAssessment",
    "SocialEngineeringSignal",
    "SocialEngineeringSignalDetector",
]
