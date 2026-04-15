"""Governance review workflow for experiment promotion decisions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Literal

from .experiment_approval_controls import ExperimentApprovalRequest
from .failure_taxonomy import FailureLabelingReport
from .quarterly_gap_report import QuarterlyGapReport
from .safety_regression_gate import SafetyRegressionGateResult

GovernanceChecklistStatus = Literal["pass", "warn", "fail"]
GovernanceRecommendation = Literal["approve", "hold", "reject"]
GovernanceReviewStatus = Literal["open", "approved", "hold", "rejected"]
GovernanceDecision = Literal["approve", "hold", "reject"]

_ALLOWED_RISK_TIERS = {"low", "medium", "high", "critical"}
_ALLOWED_REVIEWER_ROLES = {"primary_user", "authorized_operator", "system", "limited_user"}
_ALLOWED_SIGNOFF_ROLES = {"primary_user", "authorized_operator", "system"}
_ALLOWED_DECISIONS = {"approve", "hold", "reject"}


@dataclass(frozen=True)
class GovernanceChecklistItem:
    item_id: str
    title: str
    status: GovernanceChecklistStatus
    detail: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class GovernanceReviewSignoff:
    signoff_id: str
    reviewer_id: str
    reviewer_role: str
    signed_at: str
    note: str | None


@dataclass(frozen=True)
class ExperimentPromotionGovernanceReview:
    review_id: str
    request_id: str
    run_id: str
    experiment_id: str
    risk_tier: str
    created_at: str
    status: GovernanceReviewStatus
    recommendation: GovernanceRecommendation
    checklist: tuple[GovernanceChecklistItem, ...]
    required_signoff_roles: tuple[str, ...]
    signoffs: tuple[GovernanceReviewSignoff, ...]
    finalized_at: str | None
    finalized_by: str | None
    decision_note: str | None
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "risk_tier": self.risk_tier,
            "created_at": self.created_at,
            "status": self.status,
            "recommendation": self.recommendation,
            "checklist": [
                {
                    "item_id": item.item_id,
                    "title": item.title,
                    "status": item.status,
                    "detail": item.detail,
                    "metadata": dict(item.metadata),
                }
                for item in sorted(self.checklist, key=lambda entry: entry.item_id)
            ],
            "required_signoff_roles": list(self.required_signoff_roles),
            "signoffs": [
                {
                    "signoff_id": signoff.signoff_id,
                    "reviewer_id": signoff.reviewer_id,
                    "reviewer_role": signoff.reviewer_role,
                    "signed_at": signoff.signed_at,
                    "note": signoff.note,
                }
                for signoff in sorted(self.signoffs, key=lambda entry: entry.signoff_id)
            ],
            "finalized_at": self.finalized_at,
            "finalized_by": self.finalized_by,
            "decision_note": self.decision_note,
            "metadata": dict(self.metadata),
        }


class GovernanceReviewWorkflowError(ValueError):
    """Raised when governance review workflow operations are invalid."""


class ExperimentPromotionGovernanceWorkflow:
    """Creates governance reviews that gate experiment promotion decisions."""

    def __init__(self) -> None:
        self._reviews: dict[str, ExperimentPromotionGovernanceReview] = {}

    def create_review(
        self,
        *,
        request: ExperimentApprovalRequest,
        gate_result: SafetyRegressionGateResult,
        quarterly_gap_report: QuarterlyGapReport,
        failure_report: FailureLabelingReport | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExperimentPromotionGovernanceReview:
        if not isinstance(request, ExperimentApprovalRequest):
            raise TypeError("request must be ExperimentApprovalRequest")
        if not isinstance(gate_result, SafetyRegressionGateResult):
            raise TypeError("gate_result must be SafetyRegressionGateResult")
        if not isinstance(quarterly_gap_report, QuarterlyGapReport):
            raise TypeError("quarterly_gap_report must be QuarterlyGapReport")
        if not isinstance(failure_report, (FailureLabelingReport, type(None))):
            raise TypeError("failure_report must be FailureLabelingReport or None")

        normalized_risk_tier = _normalize_required(request.risk_tier, "request.risk_tier").lower()
        if normalized_risk_tier not in _ALLOWED_RISK_TIERS:
            allowed = ", ".join(sorted(_ALLOWED_RISK_TIERS))
            raise GovernanceReviewWorkflowError(
                f"Unsupported request risk_tier {normalized_risk_tier}. Allowed: {allowed}"
            )

        checklist = _build_checklist(
            request=request,
            gate_result=gate_result,
            quarterly_gap_report=quarterly_gap_report,
            failure_report=failure_report,
        )
        recommendation = _derive_recommendation(checklist)
        required_signoff_roles = _required_signoff_roles(normalized_risk_tier)

        review_index = len(self._reviews) + 1
        review_id = f"governance-review-{review_index:04d}-{request.experiment_id}"
        review = ExperimentPromotionGovernanceReview(
            review_id=review_id,
            request_id=request.request_id,
            run_id=request.run_id,
            experiment_id=request.experiment_id,
            risk_tier=normalized_risk_tier,
            created_at=_utc_now_iso(),
            status="open",
            recommendation=recommendation,
            checklist=checklist,
            required_signoff_roles=required_signoff_roles,
            signoffs=(),
            finalized_at=None,
            finalized_by=None,
            decision_note=None,
            metadata=dict(metadata or {}),
        )
        self._reviews[review.review_id] = review
        return review

    def add_signoff(
        self,
        review_id: str,
        *,
        reviewer_id: str,
        reviewer_role: str,
        note: str | None = None,
    ) -> ExperimentPromotionGovernanceReview:
        review = self.get_review(review_id)
        if review.status != "open":
            raise GovernanceReviewWorkflowError(
                f"Review {review.review_id} is {review.status}; signoffs require open status"
            )

        normalized_reviewer_id = _normalize_required(reviewer_id, "reviewer_id")
        normalized_reviewer_role = _normalize_required(reviewer_role, "reviewer_role").lower()
        if normalized_reviewer_role not in _ALLOWED_REVIEWER_ROLES:
            allowed = ", ".join(sorted(_ALLOWED_REVIEWER_ROLES))
            raise GovernanceReviewWorkflowError(
                f"Unsupported reviewer_role {normalized_reviewer_role}. Allowed: {allowed}"
            )
        if normalized_reviewer_role not in _ALLOWED_SIGNOFF_ROLES:
            raise GovernanceReviewWorkflowError(
                f"Role {normalized_reviewer_role} cannot sign governance reviews"
            )

        for signoff in review.signoffs:
            if signoff.reviewer_id == normalized_reviewer_id:
                raise GovernanceReviewWorkflowError(
                    f"Reviewer {normalized_reviewer_id} already signed review {review.review_id}"
                )

        signoff = GovernanceReviewSignoff(
            signoff_id=f"{review.review_id}-sig-{len(review.signoffs) + 1:02d}",
            reviewer_id=normalized_reviewer_id,
            reviewer_role=normalized_reviewer_role,
            signed_at=_utc_now_iso(),
            note=_normalize_optional(note),
        )

        updated = replace(
            review,
            signoffs=review.signoffs + (signoff,),
        )
        self._reviews[review.review_id] = updated
        return updated

    def finalize_review(
        self,
        review_id: str,
        *,
        decision: GovernanceDecision | str,
        reviewer_id: str,
        reviewer_role: str,
        note: str,
        allow_override: bool = False,
    ) -> ExperimentPromotionGovernanceReview:
        review = self.get_review(review_id)
        if review.status != "open":
            raise GovernanceReviewWorkflowError(
                f"Review {review.review_id} is {review.status}; only open reviews can be finalized"
            )

        normalized_decision = _normalize_required(decision, "decision").lower()
        if normalized_decision not in _ALLOWED_DECISIONS:
            allowed = ", ".join(sorted(_ALLOWED_DECISIONS))
            raise GovernanceReviewWorkflowError(
                f"Unsupported decision {normalized_decision}. Allowed: {allowed}"
            )

        normalized_reviewer_id = _normalize_required(reviewer_id, "reviewer_id")
        normalized_reviewer_role = _normalize_required(reviewer_role, "reviewer_role").lower()
        if normalized_reviewer_role not in _ALLOWED_SIGNOFF_ROLES:
            raise GovernanceReviewWorkflowError(
                f"Role {normalized_reviewer_role} cannot finalize governance reviews"
            )

        normalized_note = _normalize_required(note, "note")
        has_override = bool(allow_override)

        present_roles = {signoff.reviewer_role for signoff in review.signoffs}
        present_roles.add(normalized_reviewer_role)
        missing_required_roles = sorted(
            role for role in review.required_signoff_roles if role not in present_roles
        )
        if missing_required_roles:
            raise GovernanceReviewWorkflowError(
                "Missing required signoff roles: " + ", ".join(missing_required_roles)
            )

        if review.recommendation != "approve" and normalized_decision == "approve":
            if not has_override:
                raise GovernanceReviewWorkflowError(
                    f"Review recommendation is {review.recommendation}; approve requires allow_override"
                )

        finalized_status: GovernanceReviewStatus
        if normalized_decision == "approve":
            finalized_status = "approved"
        elif normalized_decision == "hold":
            finalized_status = "hold"
        else:
            finalized_status = "rejected"

        merged_metadata = dict(review.metadata)
        merged_metadata["final_decision"] = normalized_decision
        merged_metadata["recommendation"] = review.recommendation
        merged_metadata["override_used"] = has_override

        updated = replace(
            review,
            status=finalized_status,
            finalized_at=_utc_now_iso(),
            finalized_by=f"{normalized_reviewer_id}:{normalized_reviewer_role}",
            decision_note=normalized_note,
            metadata=merged_metadata,
        )
        self._reviews[review.review_id] = updated
        return updated

    def get_review(self, review_id: str) -> ExperimentPromotionGovernanceReview:
        normalized_review_id = _normalize_required(review_id, "review_id").lower()
        review = self._reviews.get(normalized_review_id)
        if review is None:
            raise KeyError(f"Unknown governance review: {normalized_review_id}")
        return review

    def list_reviews(
        self,
        *,
        status: GovernanceReviewStatus | None = None,
        recommendation: GovernanceRecommendation | None = None,
        experiment_id: str | None = None,
    ) -> list[ExperimentPromotionGovernanceReview]:
        reviews = list(self._reviews.values())

        if status is not None:
            normalized_status = _normalize_required(status, "status").lower()
            reviews = [review for review in reviews if review.status == normalized_status]

        if recommendation is not None:
            normalized_recommendation = _normalize_required(recommendation, "recommendation").lower()
            reviews = [
                review for review in reviews if review.recommendation == normalized_recommendation
            ]

        if experiment_id is not None:
            normalized_experiment_id = _normalize_required(experiment_id, "experiment_id").lower()
            reviews = [
                review for review in reviews if review.experiment_id == normalized_experiment_id
            ]

        return sorted(reviews, key=lambda item: item.review_id)


def _build_checklist(
    *,
    request: ExperimentApprovalRequest,
    gate_result: SafetyRegressionGateResult,
    quarterly_gap_report: QuarterlyGapReport,
    failure_report: FailureLabelingReport | None,
) -> tuple[GovernanceChecklistItem, ...]:
    checklist: list[GovernanceChecklistItem] = []

    approval_status: GovernanceChecklistStatus = "pass" if request.status == "approved" else "fail"
    checklist.append(
        GovernanceChecklistItem(
            item_id="approval-status",
            title="Approval Request Status",
            status=approval_status,
            detail=f"Approval request status is {request.status}.",
            metadata={"request_status": request.status},
        )
    )

    risk_alignment_status: GovernanceChecklistStatus = (
        "pass" if request.risk_tier == gate_result.risk_tier else "fail"
    )
    checklist.append(
        GovernanceChecklistItem(
            item_id="risk-tier-alignment",
            title="Risk Tier Alignment",
            status=risk_alignment_status,
            detail=(
                f"Request risk_tier={request.risk_tier}, safety risk_tier={gate_result.risk_tier}."
            ),
            metadata={
                "request_risk_tier": request.risk_tier,
                "safety_risk_tier": gate_result.risk_tier,
            },
        )
    )

    safety_status: GovernanceChecklistStatus = "pass" if gate_result.decision == "allow" else "fail"
    checklist.append(
        GovernanceChecklistItem(
            item_id="safety-gate",
            title="Safety Regression Gate",
            status=safety_status,
            detail=(
                f"Safety gate decision={gate_result.decision} with {len(gate_result.violations)} violation(s)."
            ),
            metadata={
                "gate_id": gate_result.gate_id,
                "decision": gate_result.decision,
                "violation_count": len(gate_result.violations),
            },
        )
    )

    gap_status: GovernanceChecklistStatus
    if quarterly_gap_report.overall_status == "met":
        gap_status = "pass"
    elif quarterly_gap_report.overall_status == "near":
        gap_status = "warn"
    else:
        gap_status = "fail"
    checklist.append(
        GovernanceChecklistItem(
            item_id="quarterly-gap",
            title="Quarterly Capability Gap",
            status=gap_status,
            detail=(
                f"Quarterly status={quarterly_gap_report.overall_status}, "
                f"gap={quarterly_gap_report.overall_gap_to_target:+.4f}."
            ),
            metadata={
                "report_id": quarterly_gap_report.report_id,
                "overall_status": quarterly_gap_report.overall_status,
                "overall_gap_to_target": quarterly_gap_report.overall_gap_to_target,
            },
        )
    )

    risk_status: GovernanceChecklistStatus
    if quarterly_gap_report.risk_level == "low":
        risk_status = "pass"
    elif quarterly_gap_report.risk_level == "moderate":
        risk_status = "warn"
    else:
        risk_status = "fail"
    checklist.append(
        GovernanceChecklistItem(
            item_id="quarterly-risk",
            title="Quarterly Risk Level",
            status=risk_status,
            detail=f"Quarterly risk level is {quarterly_gap_report.risk_level}.",
            metadata={"risk_level": quarterly_gap_report.risk_level},
        )
    )

    failure_status, failure_detail, failure_metadata = _failure_signal_review(
        failure_report=failure_report,
        expected_taxonomy_version=quarterly_gap_report.taxonomy_version,
    )
    checklist.append(
        GovernanceChecklistItem(
            item_id="failure-signals",
            title="Failure Signal Review",
            status=failure_status,
            detail=failure_detail,
            metadata=failure_metadata,
        )
    )

    return tuple(checklist)


def _failure_signal_review(
    *,
    failure_report: FailureLabelingReport | None,
    expected_taxonomy_version: str,
) -> tuple[GovernanceChecklistStatus, str, dict[str, Any]]:
    if failure_report is None:
        return (
            "pass",
            "No failure labeling report supplied.",
            {"failure_report_present": False},
        )

    if failure_report.taxonomy_version != expected_taxonomy_version:
        return (
            "fail",
            (
                "Failure report taxonomy_version does not match quarterly report "
                f"(expected {expected_taxonomy_version}, got {failure_report.taxonomy_version})."
            ),
            {
                "failure_report_present": True,
                "taxonomy_version": failure_report.taxonomy_version,
                "expected_taxonomy_version": expected_taxonomy_version,
            },
        )

    severity_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    max_rank = 0
    high_or_critical_count = 0
    for label in failure_report.labels:
        rank = severity_rank.get(label.severity, 0)
        max_rank = max(max_rank, rank)
        if label.severity in {"high", "critical"}:
            high_or_critical_count += 1

    if max_rank >= severity_rank["critical"]:
        status: GovernanceChecklistStatus = "fail"
    elif max_rank >= severity_rank["high"]:
        status = "warn"
    else:
        status = "pass"

    detail = (
        f"Failure labels={len(failure_report.labels)}, high_or_critical={high_or_critical_count}, "
        f"unmatched_signals={len(failure_report.unmatched_signal_ids)}."
    )
    metadata = {
        "failure_report_present": True,
        "taxonomy_version": failure_report.taxonomy_version,
        "label_count": len(failure_report.labels),
        "high_or_critical_count": high_or_critical_count,
        "unmatched_signal_count": len(failure_report.unmatched_signal_ids),
    }
    return status, detail, metadata


def _derive_recommendation(
    checklist: tuple[GovernanceChecklistItem, ...],
) -> GovernanceRecommendation:
    if any(item.status == "fail" for item in checklist):
        return "reject"
    if any(item.status == "warn" for item in checklist):
        return "hold"
    return "approve"


def _required_signoff_roles(risk_tier: str) -> tuple[str, ...]:
    normalized_risk_tier = _normalize_required(risk_tier, "risk_tier").lower()
    if normalized_risk_tier == "low":
        return ("primary_user",)
    if normalized_risk_tier in {"medium", "high"}:
        return ("authorized_operator", "primary_user")
    return ("authorized_operator", "primary_user", "system")


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise GovernanceReviewWorkflowError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "GovernanceChecklistStatus",
    "GovernanceRecommendation",
    "GovernanceReviewStatus",
    "GovernanceDecision",
    "GovernanceChecklistItem",
    "GovernanceReviewSignoff",
    "ExperimentPromotionGovernanceReview",
    "GovernanceReviewWorkflowError",
    "ExperimentPromotionGovernanceWorkflow",
]