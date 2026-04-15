"""Ethical refusal evaluator with safe alternative-path checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

RefusalDecisionStatus = Literal["allow", "refuse"]
AlternativeCheckStatus = Literal["pass", "fail"]


@dataclass(frozen=True)
class EthicalRefusalRequest:
    request_id: str
    profile_id: str
    mode: str
    prompt: str
    source: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SafeAlternativePath:
    path_id: str
    path_text: str
    category: str


@dataclass(frozen=True)
class AlternativePathCheck:
    check_id: str
    title: str
    status: AlternativeCheckStatus
    detail: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class EthicalRefusalDecision:
    decision_id: str
    request_id: str
    profile_id: str
    mode: str
    status: RefusalDecisionStatus
    reason_code: str
    reason: str
    refusal_message: str
    safety_score: float
    deterministic_digest: str
    safe_alternatives: tuple[SafeAlternativePath, ...]
    alternative_checks: tuple[AlternativePathCheck, ...]
    created_at: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "request_id": self.request_id,
            "profile_id": self.profile_id,
            "mode": self.mode,
            "status": self.status,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "refusal_message": self.refusal_message,
            "safety_score": self.safety_score,
            "deterministic_digest": self.deterministic_digest,
            "safe_alternatives": [
                {
                    "path_id": path.path_id,
                    "path_text": path.path_text,
                    "category": path.category,
                }
                for path in sorted(self.safe_alternatives, key=lambda item: item.path_id)
            ],
            "alternative_checks": [
                {
                    "check_id": check.check_id,
                    "title": check.title,
                    "status": check.status,
                    "detail": check.detail,
                    "metadata": dict(check.metadata),
                }
                for check in sorted(self.alternative_checks, key=lambda item: item.check_id)
            ],
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


class EthicalRefusalError(ValueError):
    """Raised when ethical refusal evaluation inputs are invalid."""


class EthicalRefusalEvaluator:
    """Evaluates whether a request should be refused and routes safe alternatives."""

    def __init__(self) -> None:
        self._unsafe_patterns = _unsafe_patterns()
        self._default_safe_paths = _default_safe_paths()
        self._fallback_safe_paths = (
            "I can help with a safe, policy-compliant plan for this objective.",
            "I can help you reframe the request toward defensive, legal, and ethical outcomes.",
            "I can help produce a risk assessment and mitigation checklist instead.",
        )

    def evaluate_request(
        self,
        request: EthicalRefusalRequest,
        *,
        proposed_alternative_paths: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EthicalRefusalDecision:
        normalized = _normalize_request(request)

        violation_category = self._detect_violation(normalized.prompt)
        if violation_category is None:
            digest = _build_decision_digest(
                request_id=normalized.request_id,
                profile_id=normalized.profile_id,
                mode=normalized.mode,
                status="allow",
                reason_code="policy.allow",
                safe_alternatives=(),
            )
            checks = (
                AlternativePathCheck(
                    check_id="alternative-path-not-required",
                    title="Alternative Path Requirement",
                    status="pass",
                    detail="Request is policy-compliant. Refusal and alternatives are not required.",
                    metadata={"required": False},
                ),
            )
            return EthicalRefusalDecision(
                decision_id=f"ethical-refusal-{digest[:20]}",
                request_id=normalized.request_id,
                profile_id=normalized.profile_id,
                mode=normalized.mode,
                status="allow",
                reason_code="policy.allow",
                reason="Request is allowed under current policy constraints.",
                refusal_message="",
                safety_score=1.0,
                deterministic_digest=digest,
                safe_alternatives=(),
                alternative_checks=checks,
                created_at=_utc_now_iso(),
                metadata=dict(metadata or {}),
            )

        candidates = _normalize_alternative_candidates(proposed_alternative_paths)
        if not candidates:
            candidates = tuple(self._default_safe_paths.get(violation_category, self._fallback_safe_paths))

        safe_paths: list[SafeAlternativePath] = []
        rejected_count = 0
        for index, candidate in enumerate(candidates, start=1):
            if self._detect_violation(candidate) is not None:
                rejected_count += 1
                continue
            safe_paths.append(
                SafeAlternativePath(
                    path_id=f"ALT-{index:03d}",
                    path_text=candidate,
                    category=violation_category,
                )
            )

        if not safe_paths:
            safe_paths = [
                SafeAlternativePath(
                    path_id=f"ALT-FALLBACK-{index:03d}",
                    path_text=text,
                    category="fallback",
                )
                for index, text in enumerate(self._fallback_safe_paths, start=1)
            ]

        safety_score = round(len(safe_paths) / max(1, len(candidates)), 4)
        checks = (
            AlternativePathCheck(
                check_id="alternative-path-presence",
                title="Alternative Path Presence",
                status="pass" if safe_paths else "fail",
                detail=f"Safe alternatives generated: {len(safe_paths)}.",
                metadata={"safe_alternative_count": len(safe_paths)},
            ),
            AlternativePathCheck(
                check_id="alternative-path-safety",
                title="Alternative Path Safety",
                status="pass" if rejected_count == 0 else "fail",
                detail=(
                    "All proposed alternatives were safe."
                    if rejected_count == 0
                    else f"Rejected {rejected_count} unsafe proposed alternative path(s)."
                ),
                metadata={"rejected_count": rejected_count},
            ),
        )

        digest = _build_decision_digest(
            request_id=normalized.request_id,
            profile_id=normalized.profile_id,
            mode=normalized.mode,
            status="refuse",
            reason_code=f"policy.unsafe.{violation_category}",
            safe_alternatives=tuple(path.path_text for path in safe_paths),
        )
        refusal_message = _build_refusal_message(safe_paths)

        return EthicalRefusalDecision(
            decision_id=f"ethical-refusal-{digest[:20]}",
            request_id=normalized.request_id,
            profile_id=normalized.profile_id,
            mode=normalized.mode,
            status="refuse",
            reason_code=f"policy.unsafe.{violation_category}",
            reason="Request conflicts with safety and ethical policy requirements.",
            refusal_message=refusal_message,
            safety_score=safety_score,
            deterministic_digest=digest,
            safe_alternatives=tuple(safe_paths),
            alternative_checks=checks,
            created_at=_utc_now_iso(),
            metadata=dict(metadata or {}),
        )

    def _detect_violation(self, text: str) -> str | None:
        normalized = text.lower()
        for category, patterns in self._unsafe_patterns.items():
            for pattern in patterns:
                if pattern.search(normalized) is not None:
                    return category
        return None


def _build_refusal_message(paths: list[SafeAlternativePath]) -> str:
    lines = [
        "I can't assist with that request directly due to safety policy.",
        "Safe alternatives:",
    ]
    for index, path in enumerate(paths, start=1):
        lines.append(f"{index}. {path.path_text}")
    return "\n".join(lines)


def _normalize_request(request: EthicalRefusalRequest) -> EthicalRefusalRequest:
    if not isinstance(request, EthicalRefusalRequest):
        raise TypeError("request must be an EthicalRefusalRequest")

    return EthicalRefusalRequest(
        request_id=_normalize_required(request.request_id, "request_id"),
        profile_id=_normalize_required(request.profile_id, "profile_id").lower(),
        mode=_normalize_required(request.mode, "mode").lower(),
        prompt=_normalize_required(request.prompt, "prompt"),
        source=_normalize_required(request.source, "source").lower(),
        metadata=dict(request.metadata),
    )


def _normalize_alternative_candidates(
    candidates: list[str] | tuple[str, ...] | None,
) -> tuple[str, ...]:
    if candidates is None:
        return ()
    if not isinstance(candidates, (list, tuple)):
        raise TypeError("proposed_alternative_paths must be a list or tuple of strings")

    normalized: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        text = _normalize_required(value, "proposed_alternative_path")
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return tuple(normalized)


def _build_decision_digest(
    *,
    request_id: str,
    profile_id: str,
    mode: str,
    status: RefusalDecisionStatus,
    reason_code: str,
    safe_alternatives: tuple[str, ...],
) -> str:
    canonical = json.dumps(
        {
            "request_id": request_id,
            "profile_id": profile_id,
            "mode": mode,
            "status": status,
            "reason_code": reason_code,
            "safe_alternatives": list(safe_alternatives),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _unsafe_patterns() -> dict[str, tuple[re.Pattern[str], ...]]:
    return {
        "cyber_abuse": (
            re.compile(r"\b(hack|exploit|backdoor|malware|ransomware|credential theft|ddos|phish)\b"),
            re.compile(r"\b(bypass|disable)\b.{0,40}\b(security|auth|authentication|guard)\b"),
        ),
        "violence": (
            re.compile(r"\b(kill|harm|injure|weapon|bomb|assassinate)\b"),
        ),
        "privacy_abuse": (
            re.compile(r"\b(dox|stalk|track someone|steal personal data|spy on)\b"),
        ),
        "financial_fraud": (
            re.compile(r"\b(fraud|scam|launder|carding|embezzle)\b"),
        ),
    }


def _default_safe_paths() -> dict[str, tuple[str, ...]]:
    return {
        "cyber_abuse": (
            "I can help you build a defensive security hardening checklist for systems you own.",
            "I can help interpret authorized vulnerability scan results and prioritize remediations.",
            "I can help draft an incident-response containment and recovery plan.",
        ),
        "violence": (
            "I can help with de-escalation language and a non-violent safety plan.",
            "I can help identify emergency and crisis-support resources in your region.",
            "I can help draft a conflict-resolution checklist for a safer outcome.",
        ),
        "privacy_abuse": (
            "I can help strengthen account privacy settings and personal data protections.",
            "I can help draft a lawful process for requesting data through authorized channels.",
            "I can help design a consent-based communication and record-keeping workflow.",
        ),
        "financial_fraud": (
            "I can help identify fraud indicators and create prevention controls.",
            "I can help draft a compliance-focused audit trail and reporting process.",
            "I can help produce a risk register for legal and ethical financial operations.",
        ),
    }


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise EthicalRefusalError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "RefusalDecisionStatus",
    "AlternativeCheckStatus",
    "EthicalRefusalRequest",
    "SafeAlternativePath",
    "AlternativePathCheck",
    "EthicalRefusalDecision",
    "EthicalRefusalError",
    "EthicalRefusalEvaluator",
]
