"""Persona compliance evaluator across FRIDAY and JARVIS profiles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from .profile_engine import PersonaProfileEngine

ComplianceStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class PersonaComplianceSample:
    sample_id: str
    profile_id: str
    addressed_to: str
    response_text: str
    response_tags: tuple[str, ...]
    mode: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PersonaComplianceCheck:
    check_id: str
    title: str
    status: ComplianceStatus
    score: float
    detail: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PersonaComplianceReport:
    report_id: str
    profile_id: str
    generated_at: str
    sample_count: int
    pass_count: int
    warn_count: int
    fail_count: int
    compliance_score: float
    status: ComplianceStatus
    deterministic_digest: str
    checks: tuple[PersonaComplianceCheck, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "profile_id": self.profile_id,
            "generated_at": self.generated_at,
            "sample_count": self.sample_count,
            "pass_count": self.pass_count,
            "warn_count": self.warn_count,
            "fail_count": self.fail_count,
            "compliance_score": self.compliance_score,
            "status": self.status,
            "deterministic_digest": self.deterministic_digest,
            "checks": [
                {
                    "check_id": check.check_id,
                    "title": check.title,
                    "status": check.status,
                    "score": check.score,
                    "detail": check.detail,
                    "metadata": dict(check.metadata),
                }
                for check in sorted(self.checks, key=lambda item: item.check_id)
            ],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PersonaComplianceBatchReport:
    batch_id: str
    generated_at: str
    overall_score: float
    overall_status: ComplianceStatus
    reports: tuple[PersonaComplianceReport, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "generated_at": self.generated_at,
            "overall_score": self.overall_score,
            "overall_status": self.overall_status,
            "reports": [
                report.to_manifest()
                for report in sorted(self.reports, key=lambda item: item.profile_id)
            ],
            "metadata": dict(self.metadata),
        }


class PersonaComplianceError(ValueError):
    """Raised when persona compliance inputs are invalid."""


class PersonaComplianceEvaluator:
    """Evaluates persona behavior compliance for FRIDAY and JARVIS profiles."""

    def __init__(self, profile_engine: PersonaProfileEngine | None = None) -> None:
        self.profile_engine = profile_engine or PersonaProfileEngine()

    def evaluate_profile(
        self,
        profile_id: str,
        samples: list[PersonaComplianceSample] | tuple[PersonaComplianceSample, ...],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> PersonaComplianceReport:
        normalized_profile = _normalize_required(profile_id, "profile_id").lower()
        profile = self.profile_engine.select_profile(normalized_profile)
        normalized_samples = _normalize_samples(samples, expected_profile_id=normalized_profile)

        checks = (
            _check_sample_coverage(normalized_samples),
            _check_addressing(profile.profile_id, profile.addressing_default, normalized_samples),
            _check_persona_tag(profile.profile_id, normalized_samples),
            _check_confidence_tag(normalized_samples),
        )

        pass_count = sum(1 for check in checks if check.status == "pass")
        warn_count = sum(1 for check in checks if check.status == "warn")
        fail_count = sum(1 for check in checks if check.status == "fail")

        compliance_score = round(sum(check.score for check in checks) / len(checks), 4)
        if fail_count > 0:
            status: ComplianceStatus = "fail"
        elif warn_count > 0:
            status = "warn"
        else:
            status = "pass"

        deterministic_digest = _build_report_digest(
            profile_id=normalized_profile,
            checks=checks,
            sample_count=len(normalized_samples),
        )

        return PersonaComplianceReport(
            report_id=f"persona-compliance-{deterministic_digest[:20]}",
            profile_id=normalized_profile,
            generated_at=_utc_now_iso(),
            sample_count=len(normalized_samples),
            pass_count=pass_count,
            warn_count=warn_count,
            fail_count=fail_count,
            compliance_score=compliance_score,
            status=status,
            deterministic_digest=deterministic_digest,
            checks=checks,
            metadata=dict(metadata or {}),
        )

    def evaluate_standard_profiles(
        self,
        samples_by_profile: dict[str, list[PersonaComplianceSample] | tuple[PersonaComplianceSample, ...]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> PersonaComplianceBatchReport:
        if not isinstance(samples_by_profile, dict):
            raise TypeError("samples_by_profile must be a dict")

        reports = (
            self.evaluate_profile("friday", samples_by_profile.get("friday", ())),
            self.evaluate_profile("jarvis", samples_by_profile.get("jarvis", ())),
        )

        overall_score = round(sum(report.compliance_score for report in reports) / len(reports), 4)
        if any(report.status == "fail" for report in reports):
            overall_status: ComplianceStatus = "fail"
        elif any(report.status == "warn" for report in reports):
            overall_status = "warn"
        else:
            overall_status = "pass"

        digest = _build_batch_digest(reports=reports, overall_score=overall_score, overall_status=overall_status)

        return PersonaComplianceBatchReport(
            batch_id=f"persona-batch-{digest[:20]}",
            generated_at=_utc_now_iso(),
            overall_score=overall_score,
            overall_status=overall_status,
            reports=tuple(sorted(reports, key=lambda item: item.profile_id)),
            metadata=dict(metadata or {}),
        )


def _check_sample_coverage(samples: tuple[PersonaComplianceSample, ...]) -> PersonaComplianceCheck:
    if not samples:
        return PersonaComplianceCheck(
            check_id="sample-coverage",
            title="Conversation Sample Coverage",
            status="fail",
            score=0.0,
            detail="No conversation samples were provided for persona compliance evaluation.",
            metadata={"sample_count": 0},
        )

    score = 1.0 if len(samples) >= 3 else round(len(samples) / 3.0, 4)
    status: ComplianceStatus
    if score >= 1.0:
        status = "pass"
    else:
        status = "warn"

    return PersonaComplianceCheck(
        check_id="sample-coverage",
        title="Conversation Sample Coverage",
        status=status,
        score=score,
        detail=f"Sample count={len(samples)}.",
        metadata={"sample_count": len(samples)},
    )


def _check_addressing(
    profile_id: str,
    default_address: str,
    samples: tuple[PersonaComplianceSample, ...],
) -> PersonaComplianceCheck:
    if not samples:
        return PersonaComplianceCheck(
            check_id="addressing-consistency",
            title="Addressing Consistency",
            status="fail",
            score=0.0,
            detail="No samples available to evaluate addressing behavior.",
            metadata={},
        )

    if profile_id == "jarvis":
        allowed = {"sir", "maam", "sir or maam"}
    else:
        allowed = {default_address.lower()}

    compliant = sum(1 for sample in samples if sample.addressed_to.lower() in allowed)
    ratio = round(compliant / len(samples), 4)

    status: ComplianceStatus
    if ratio >= 0.95:
        status = "pass"
    elif ratio >= 0.60:
        status = "warn"
    else:
        status = "fail"

    return PersonaComplianceCheck(
        check_id="addressing-consistency",
        title="Addressing Consistency",
        status=status,
        score=ratio,
        detail=f"Addressing compliance ratio={ratio:.2f}.",
        metadata={"compliant_samples": compliant, "total_samples": len(samples)},
    )


def _check_persona_tag(profile_id: str, samples: tuple[PersonaComplianceSample, ...]) -> PersonaComplianceCheck:
    if not samples:
        return PersonaComplianceCheck(
            check_id="persona-tag",
            title="Persona Tag Presence",
            status="fail",
            score=0.0,
            detail="No samples available to evaluate persona tagging.",
            metadata={},
        )

    expected_tag = f"persona:{profile_id}"
    compliant = sum(1 for sample in samples if expected_tag in sample.response_tags)
    ratio = round(compliant / len(samples), 4)

    status: ComplianceStatus
    if ratio >= 0.95:
        status = "pass"
    elif ratio >= 0.75:
        status = "warn"
    else:
        status = "fail"

    return PersonaComplianceCheck(
        check_id="persona-tag",
        title="Persona Tag Presence",
        status=status,
        score=ratio,
        detail=f"Persona tag compliance ratio={ratio:.2f}.",
        metadata={"expected_tag": expected_tag},
    )


def _check_confidence_tag(samples: tuple[PersonaComplianceSample, ...]) -> PersonaComplianceCheck:
    if not samples:
        return PersonaComplianceCheck(
            check_id="confidence-tag",
            title="Confidence Tag Presence",
            status="fail",
            score=0.0,
            detail="No samples available to evaluate confidence tagging.",
            metadata={},
        )

    compliant = sum(1 for sample in samples if "[confidence:" in sample.response_text.lower())
    ratio = round(compliant / len(samples), 4)

    status: ComplianceStatus
    if ratio >= 0.95:
        status = "pass"
    elif ratio >= 0.75:
        status = "warn"
    else:
        status = "fail"

    return PersonaComplianceCheck(
        check_id="confidence-tag",
        title="Confidence Tag Presence",
        status=status,
        score=ratio,
        detail=f"Confidence tag compliance ratio={ratio:.2f}.",
        metadata={"compliant_samples": compliant, "total_samples": len(samples)},
    )


def _normalize_samples(
    samples: list[PersonaComplianceSample] | tuple[PersonaComplianceSample, ...],
    *,
    expected_profile_id: str,
) -> tuple[PersonaComplianceSample, ...]:
    if not isinstance(samples, (list, tuple)):
        raise TypeError("samples must be a list or tuple of PersonaComplianceSample")

    normalized: list[PersonaComplianceSample] = []
    seen_sample_ids: set[str] = set()
    for sample in samples:
        if not isinstance(sample, PersonaComplianceSample):
            raise TypeError("samples must contain PersonaComplianceSample values")

        sample_id = _normalize_required(sample.sample_id, "sample_id")
        if sample_id in seen_sample_ids:
            raise PersonaComplianceError(f"Duplicate sample_id: {sample_id}")
        seen_sample_ids.add(sample_id)

        profile_id = _normalize_required(sample.profile_id, "profile_id").lower()
        if profile_id != expected_profile_id:
            raise PersonaComplianceError(
                f"Sample {sample_id} profile_id {profile_id} does not match expected {expected_profile_id}"
            )

        addressed_to = _normalize_required(sample.addressed_to, "addressed_to")
        response_text = _normalize_required(sample.response_text, "response_text")
        tags = tuple(sorted({_normalize_required(tag, "response_tag").lower() for tag in sample.response_tags}))

        normalized.append(
            PersonaComplianceSample(
                sample_id=sample_id,
                profile_id=profile_id,
                addressed_to=addressed_to,
                response_text=response_text,
                response_tags=tags,
                mode=_normalize_optional(sample.mode),
                metadata=dict(sample.metadata),
            )
        )

    return tuple(sorted(normalized, key=lambda item: item.sample_id))


def _build_report_digest(
    *,
    profile_id: str,
    checks: tuple[PersonaComplianceCheck, ...],
    sample_count: int,
) -> str:
    canonical = json.dumps(
        {
            "profile_id": profile_id,
            "sample_count": sample_count,
            "checks": [
                {
                    "check_id": check.check_id,
                    "status": check.status,
                    "score": check.score,
                }
                for check in sorted(checks, key=lambda item: item.check_id)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _build_batch_digest(
    *,
    reports: tuple[PersonaComplianceReport, ...],
    overall_score: float,
    overall_status: ComplianceStatus,
) -> str:
    canonical = json.dumps(
        {
            "overall_score": overall_score,
            "overall_status": overall_status,
            "reports": [
                {
                    "profile_id": report.profile_id,
                    "status": report.status,
                    "score": report.compliance_score,
                    "digest": report.deterministic_digest,
                }
                for report in sorted(reports, key=lambda item: item.profile_id)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise PersonaComplianceError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "ComplianceStatus",
    "PersonaComplianceSample",
    "PersonaComplianceCheck",
    "PersonaComplianceReport",
    "PersonaComplianceBatchReport",
    "PersonaComplianceError",
    "PersonaComplianceEvaluator",
]
