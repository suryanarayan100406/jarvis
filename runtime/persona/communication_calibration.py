"""Communication calibration tracker for tone and depth preference adaptation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from runtime.memory import PreferenceProfileMemory

_ALLOWED_TONES = {"direct", "concise", "balanced", "diplomatic", "analytical"}
_ALLOWED_DEPTH = {"overview", "balanced", "deep"}
_ALLOWED_VERBOSITY = {"brief", "standard", "detailed"}


@dataclass(frozen=True)
class CommunicationCalibrationSignal:
    signal_id: str
    subject_id: str
    preferred_tone: str | None
    preferred_depth: str | None
    preferred_verbosity: str | None
    satisfaction_score: float
    created_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CommunicationCalibrationSnapshot:
    subject_id: str
    recommended_tone: str
    recommended_depth: str
    recommended_verbosity: str
    confidence: float
    sample_size: int
    reason: str
    signal_ids: tuple[str, ...]
    resolved_at: str

    def to_manifest(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "recommended_tone": self.recommended_tone,
            "recommended_depth": self.recommended_depth,
            "recommended_verbosity": self.recommended_verbosity,
            "confidence": self.confidence,
            "sample_size": self.sample_size,
            "reason": self.reason,
            "signal_ids": list(self.signal_ids),
            "resolved_at": self.resolved_at,
        }


@dataclass(frozen=True)
class CommunicationCalibrationResult:
    snapshot: CommunicationCalibrationSnapshot
    applied: bool
    applied_fields: tuple[str, ...]


class CommunicationCalibrationError(ValueError):
    """Raised when communication calibration inputs are invalid."""


class CommunicationCalibrationTracker:
    """Tracks communication calibration signals and updates preference memory."""

    def __init__(self, profile_memory: PreferenceProfileMemory) -> None:
        if not isinstance(profile_memory, PreferenceProfileMemory):
            raise TypeError("profile_memory must be PreferenceProfileMemory")

        self.profile_memory = profile_memory
        self._signals: dict[str, list[CommunicationCalibrationSignal]] = {}

    def record_signal(self, signal: CommunicationCalibrationSignal) -> CommunicationCalibrationSignal:
        normalized = _normalize_signal(signal)
        self._signals.setdefault(normalized.subject_id, []).append(normalized)
        self._signals[normalized.subject_id].sort(key=lambda item: (item.created_at, item.signal_id))
        return normalized

    def list_signals(self, subject_id: str, *, limit: int | None = None) -> tuple[CommunicationCalibrationSignal, ...]:
        normalized_subject = _normalize_required(subject_id, "subject_id")
        signals = list(self._signals.get(normalized_subject, []))
        if limit is None:
            return tuple(signals)
        if not isinstance(limit, int):
            raise TypeError("limit must be an integer or None")
        if limit < 1:
            raise CommunicationCalibrationError("limit must be at least 1")
        return tuple(signals[:limit])

    def build_snapshot(
        self,
        *,
        subject_id: str,
        fallback_subjects: tuple[str, ...] | list[str] | None = None,
    ) -> CommunicationCalibrationSnapshot:
        normalized_subject = _normalize_required(subject_id, "subject_id")
        resolved = self.profile_memory.resolve_profile(
            subject_id=normalized_subject,
            fallback_subjects=fallback_subjects,
        )

        signals = tuple(self._signals.get(normalized_subject, []))
        if not signals:
            return CommunicationCalibrationSnapshot(
                subject_id=normalized_subject,
                recommended_tone=resolved.communication.tone,
                recommended_depth=resolved.domain_focus.depth,
                recommended_verbosity=resolved.communication.verbosity,
                confidence=0.0,
                sample_size=0,
                reason="No calibration signals available; using resolved preference profile defaults.",
                signal_ids=(),
                resolved_at=_utc_now_iso(),
            )

        tone_recommendation, tone_support = _recommend_dimension(
            [signal.preferred_tone for signal in signals],
            default=resolved.communication.tone,
        )
        depth_recommendation, depth_support = _recommend_dimension(
            [signal.preferred_depth for signal in signals],
            default=resolved.domain_focus.depth,
        )
        verbosity_recommendation, verbosity_support = _recommend_dimension(
            [signal.preferred_verbosity for signal in signals],
            default=resolved.communication.verbosity,
        )

        sample_factor = min(1.0, len(signals) / 3.0)
        confidence = round(((tone_support + depth_support + verbosity_support) / 3.0) * sample_factor, 4)

        reason = (
            "Calibration signals aggregated across tone, depth, and verbosity with "
            f"sample_size={len(signals)} and support ratios "
            f"tone={tone_support:.2f}, depth={depth_support:.2f}, verbosity={verbosity_support:.2f}."
        )

        return CommunicationCalibrationSnapshot(
            subject_id=normalized_subject,
            recommended_tone=tone_recommendation,
            recommended_depth=depth_recommendation,
            recommended_verbosity=verbosity_recommendation,
            confidence=confidence,
            sample_size=len(signals),
            reason=reason,
            signal_ids=tuple(signal.signal_id for signal in signals),
            resolved_at=_utc_now_iso(),
        )

    def apply_calibration(
        self,
        *,
        subject_id: str,
        min_confidence: float = 0.55,
        priority: int = 65,
        fallback_subjects: tuple[str, ...] | list[str] | None = None,
    ) -> CommunicationCalibrationResult:
        if not isinstance(min_confidence, (int, float)):
            raise TypeError("min_confidence must be numeric")
        if min_confidence < 0 or min_confidence > 1:
            raise CommunicationCalibrationError("min_confidence must be between 0 and 1")

        snapshot = self.build_snapshot(subject_id=subject_id, fallback_subjects=fallback_subjects)
        if snapshot.confidence < float(min_confidence):
            return CommunicationCalibrationResult(
                snapshot=snapshot,
                applied=False,
                applied_fields=(),
            )

        resolved = self.profile_memory.resolve_profile(subject_id=snapshot.subject_id, fallback_subjects=fallback_subjects)

        tone = snapshot.recommended_tone if snapshot.recommended_tone != resolved.communication.tone else None
        verbosity = (
            snapshot.recommended_verbosity
            if snapshot.recommended_verbosity != resolved.communication.verbosity
            else None
        )
        depth = snapshot.recommended_depth if snapshot.recommended_depth != resolved.domain_focus.depth else None

        applied_fields: list[str] = []
        if tone is not None or verbosity is not None:
            applied_fields.extend(
                self.profile_memory.set_communication_style(
                    subject_id=snapshot.subject_id,
                    tone=tone,
                    verbosity=verbosity,
                    response_style=None,
                    priority=priority,
                )
            )

        if depth is not None:
            applied_fields.extend(
                self.profile_memory.set_domain_focus(
                    subject_id=snapshot.subject_id,
                    topics=None,
                    depth=depth,
                    priority=priority,
                )
            )

        return CommunicationCalibrationResult(
            snapshot=snapshot,
            applied=bool(applied_fields),
            applied_fields=tuple(applied_fields),
        )


def _recommend_dimension(values: list[str | None], *, default: str) -> tuple[str, float]:
    weighted: dict[str, float] = {}
    total = 0.0

    for value in values:
        if value is None:
            continue
        weighted[value] = weighted.get(value, 0.0) + 1.0
        total += 1.0

    if total == 0:
        return default, 0.5

    top_value = sorted(weighted.items(), key=lambda item: (-item[1], item[0]))[0][0]
    support = round(weighted[top_value] / total, 4)
    return top_value, support


def _normalize_signal(signal: CommunicationCalibrationSignal) -> CommunicationCalibrationSignal:
    if not isinstance(signal, CommunicationCalibrationSignal):
        raise TypeError("signal must be CommunicationCalibrationSignal")

    signal_id = _normalize_required(signal.signal_id, "signal_id")
    subject_id = _normalize_required(signal.subject_id, "subject_id")

    preferred_tone = _normalize_optional_choice(signal.preferred_tone, "preferred_tone", _ALLOWED_TONES)
    preferred_depth = _normalize_optional_choice(signal.preferred_depth, "preferred_depth", _ALLOWED_DEPTH)
    preferred_verbosity = _normalize_optional_choice(
        signal.preferred_verbosity,
        "preferred_verbosity",
        _ALLOWED_VERBOSITY,
    )

    if not isinstance(signal.satisfaction_score, (int, float)):
        raise TypeError("satisfaction_score must be numeric")
    satisfaction_score = round(float(signal.satisfaction_score), 4)
    if satisfaction_score < 0 or satisfaction_score > 1:
        raise CommunicationCalibrationError("satisfaction_score must be between 0 and 1")

    created_at = _normalize_required(signal.created_at, "created_at")

    return CommunicationCalibrationSignal(
        signal_id=signal_id,
        subject_id=subject_id,
        preferred_tone=preferred_tone,
        preferred_depth=preferred_depth,
        preferred_verbosity=preferred_verbosity,
        satisfaction_score=satisfaction_score,
        created_at=created_at,
        metadata=dict(signal.metadata),
    )


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise CommunicationCalibrationError(f"{field_name} is required")
    return normalized


def _normalize_optional_choice(
    value: str | None,
    field_name: str,
    allowed: set[str],
) -> str | None:
    if value is None:
        return None
    normalized = _normalize_required(value, field_name).lower()
    if normalized not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise CommunicationCalibrationError(
            f"Unsupported {field_name}: {value}. Allowed: {allowed_values}"
        )
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "CommunicationCalibrationSignal",
    "CommunicationCalibrationSnapshot",
    "CommunicationCalibrationResult",
    "CommunicationCalibrationError",
    "CommunicationCalibrationTracker",
]
