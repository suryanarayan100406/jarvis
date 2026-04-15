"""Launch readiness review and sign-off workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Literal

from runtime.orchestration.disaster_recovery_runbook import DisasterRecoveryDrillResult
from runtime.orchestration.failure_injection_drills import FailureInjectionReport
from runtime.orchestration.launch_checklist import LaunchReadinessChecklist
from runtime.orchestration.operator_runbooks import OperatorRunbookBundle
from runtime.orchestration.release_pipeline import ReleasePipelineRecord

ReviewChecklistStatus = Literal["pass", "warn", "fail"]
LaunchReadinessRecommendation = Literal["approve", "hold", "reject"]
LaunchReadinessReviewStatus = Literal["open", "approved", "hold", "rejected"]
LaunchReadinessDecision = Literal["approve", "hold", "reject"]

_ALLOWED_REVIEWER_ROLES = {"primary_user", "authorized_operator", "system", "limited_user"}
_ALLOWED_SIGNOFF_ROLES = {"primary_user", "authorized_operator", "system"}
_ALLOWED_DECISIONS = {"approve", "hold", "reject"}


@dataclass(frozen=True)
class LaunchReadinessChecklistItem:
    item_id: str
    title: str
    status: ReviewChecklistStatus
    detail: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class LaunchReadinessSignoff:
    signoff_id: str
    reviewer_id: str
    reviewer_role: str
    signed_at: str
    note: str | None


@dataclass(frozen=True)
class LaunchReadinessReview:
    review_id: str
    checklist_id: str
    created_at: str
    status: LaunchReadinessReviewStatus
    recommendation: LaunchReadinessRecommendation
    checklist: tuple[LaunchReadinessChecklistItem, ...]
    required_signoff_roles: tuple[str, ...]
    signoffs: tuple[LaunchReadinessSignoff, ...]
    finalized_at: str | None
    finalized_by: str | None
    decision_note: str | None
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "checklist_id": self.checklist_id,
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


class LaunchReadinessReviewError(ValueError):
    """Raised when launch readiness review operations are invalid."""


class LaunchReadinessReviewWorkflow:
    """Creates launch readiness reviews and enforces sign-off workflow rules."""

    def __init__(self) -> None:
        self._reviews: dict[str, LaunchReadinessReview] = {}

    def create_review(
        self,
        *,
        launch_checklist: LaunchReadinessChecklist,
        disaster_recovery_result: DisasterRecoveryDrillResult,
        release_pipeline: ReleasePipelineRecord,
        failure_injection_report: FailureInjectionReport,
        operator_runbook_bundle: OperatorRunbookBundle,
        metadata: dict[str, Any] | None = None,
    ) -> LaunchReadinessReview:
        if not isinstance(launch_checklist, LaunchReadinessChecklist):
            raise TypeError("launch_checklist must be LaunchReadinessChecklist")
        if not isinstance(disaster_recovery_result, DisasterRecoveryDrillResult):
            raise TypeError("disaster_recovery_result must be DisasterRecoveryDrillResult")
        if not isinstance(release_pipeline, ReleasePipelineRecord):
            raise TypeError("release_pipeline must be ReleasePipelineRecord")
        if not isinstance(failure_injection_report, FailureInjectionReport):
            raise TypeError("failure_injection_report must be FailureInjectionReport")
        if not isinstance(operator_runbook_bundle, OperatorRunbookBundle):
            raise TypeError("operator_runbook_bundle must be OperatorRunbookBundle")

        checklist = _build_review_checklist(
            launch_checklist=launch_checklist,
            disaster_recovery_result=disaster_recovery_result,
            release_pipeline=release_pipeline,
            failure_injection_report=failure_injection_report,
            operator_runbook_bundle=operator_runbook_bundle,
        )
        recommendation = _derive_recommendation(checklist)
        required_signoff_roles = _required_signoff_roles(recommendation)

        review_id = f"launch-readiness-{len(self._reviews) + 1:04d}-{launch_checklist.checklist_id}"
        review = LaunchReadinessReview(
            review_id=review_id,
            checklist_id=launch_checklist.checklist_id,
            created_at=_utc_now_iso(),
            status="open",
            recommendation=recommendation,
            checklist=checklist,
            required_signoff_roles=required_signoff_roles,
            signoffs=(),
            finalized_at=None,
            finalized_by=None,
            decision_note=None,
            metadata={
                "launch_decision": launch_checklist.decision,
                "release_pipeline_id": release_pipeline.pipeline_id,
                "drill_id": disaster_recovery_result.drill_id,
                "failure_report_id": failure_injection_report.report_id,
                "runbook_bundle_id": operator_runbook_bundle.bundle_id,
                **dict(metadata or {}),
            },
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
    ) -> LaunchReadinessReview:
        review = self.get_review(review_id)
        if review.status != "open":
            raise LaunchReadinessReviewError(
                f"Review {review.review_id} is {review.status}; signoffs require open status"
            )

        normalized_reviewer_id = _normalize_required(reviewer_id, "reviewer_id")
        normalized_reviewer_role = _normalize_required(reviewer_role, "reviewer_role").lower()
        if normalized_reviewer_role not in _ALLOWED_REVIEWER_ROLES:
            allowed = ", ".join(sorted(_ALLOWED_REVIEWER_ROLES))
            raise LaunchReadinessReviewError(
                f"Unsupported reviewer_role {normalized_reviewer_role}. Allowed: {allowed}"
            )
        if normalized_reviewer_role not in _ALLOWED_SIGNOFF_ROLES:
            raise LaunchReadinessReviewError(
                f"Role {normalized_reviewer_role} cannot sign launch readiness reviews"
            )

        for signoff in review.signoffs:
            if signoff.reviewer_id == normalized_reviewer_id:
                raise LaunchReadinessReviewError(
                    f"Reviewer {normalized_reviewer_id} already signed review {review.review_id}"
                )

        signoff = LaunchReadinessSignoff(
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
        decision: LaunchReadinessDecision | str,
        reviewer_id: str,
        reviewer_role: str,
        note: str,
        allow_override: bool = False,
    ) -> LaunchReadinessReview:
        review = self.get_review(review_id)
        if review.status != "open":
            raise LaunchReadinessReviewError(
                f"Review {review.review_id} is {review.status}; only open reviews can be finalized"
            )

        normalized_decision = _normalize_required(decision, "decision").lower()
        if normalized_decision not in _ALLOWED_DECISIONS:
            allowed = ", ".join(sorted(_ALLOWED_DECISIONS))
            raise LaunchReadinessReviewError(
                f"Unsupported decision {normalized_decision}. Allowed: {allowed}"
            )

        normalized_reviewer_id = _normalize_required(reviewer_id, "reviewer_id")
        normalized_reviewer_role = _normalize_required(reviewer_role, "reviewer_role").lower()
        if normalized_reviewer_role not in _ALLOWED_SIGNOFF_ROLES:
            raise LaunchReadinessReviewError(
                f"Role {normalized_reviewer_role} cannot finalize launch readiness reviews"
            )

        normalized_note = _normalize_required(note, "note")

        present_roles = {signoff.reviewer_role for signoff in review.signoffs}
        present_roles.add(normalized_reviewer_role)
        missing_required_roles = sorted(
            role for role in review.required_signoff_roles if role not in present_roles
        )
        if missing_required_roles:
            raise LaunchReadinessReviewError(
                "Missing required signoff roles: " + ", ".join(missing_required_roles)
            )

        if review.recommendation != "approve" and normalized_decision == "approve" and not allow_override:
            raise LaunchReadinessReviewError(
                f"Review recommendation is {review.recommendation}; approve requires allow_override"
            )

        if normalized_decision == "approve":
            finalized_status: LaunchReadinessReviewStatus = "approved"
        elif normalized_decision == "hold":
            finalized_status = "hold"
        else:
            finalized_status = "rejected"

        merged_metadata = dict(review.metadata)
        merged_metadata["final_decision"] = normalized_decision
        merged_metadata["override_used"] = bool(allow_override)

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

    def get_review(self, review_id: str) -> LaunchReadinessReview:
        normalized_review_id = _normalize_required(review_id, "review_id").lower()
        review = self._reviews.get(normalized_review_id)
        if review is None:
            raise KeyError(f"Unknown launch readiness review: {normalized_review_id}")
        return review

    def list_reviews(
        self,
        *,
        status: LaunchReadinessReviewStatus | None = None,
        recommendation: LaunchReadinessRecommendation | None = None,
    ) -> list[LaunchReadinessReview]:
        reviews = list(self._reviews.values())

        if status is not None:
            normalized_status = _normalize_required(status, "status").lower()
            reviews = [review for review in reviews if review.status == normalized_status]

        if recommendation is not None:
            normalized_recommendation = _normalize_required(recommendation, "recommendation").lower()
            reviews = [
                review for review in reviews if review.recommendation == normalized_recommendation
            ]

        return sorted(reviews, key=lambda item: item.review_id)


def _build_review_checklist(
    *,
    launch_checklist: LaunchReadinessChecklist,
    disaster_recovery_result: DisasterRecoveryDrillResult,
    release_pipeline: ReleasePipelineRecord,
    failure_injection_report: FailureInjectionReport,
    operator_runbook_bundle: OperatorRunbookBundle,
) -> tuple[LaunchReadinessChecklistItem, ...]:
    items: list[LaunchReadinessChecklistItem] = []

    launch_gate_status: ReviewChecklistStatus
    if launch_checklist.decision == "go":
        launch_gate_status = "pass"
    elif launch_checklist.decision == "hold":
        launch_gate_status = "warn"
    else:
        launch_gate_status = "fail"

    items.append(
        LaunchReadinessChecklistItem(
            item_id="launch-gates",
            title="Launch Checklist Decision",
            status=launch_gate_status,
            detail=f"Launch checklist decision is {launch_checklist.decision}.",
            metadata={
                "checklist_id": launch_checklist.checklist_id,
                "decision": launch_checklist.decision,
                "warn_count": launch_checklist.warn_count,
                "fail_count": launch_checklist.fail_count,
            },
        )
    )

    release_status: ReviewChecklistStatus
    if release_pipeline.status == "promoted":
        release_status = "pass"
    elif release_pipeline.status == "pending_canary":
        release_status = "warn"
    else:
        release_status = "fail"

    items.append(
        LaunchReadinessChecklistItem(
            item_id="release-pipeline",
            title="Release Pipeline",
            status=release_status,
            detail=f"Release pipeline status is {release_pipeline.status}.",
            metadata={
                "pipeline_id": release_pipeline.pipeline_id,
                "status": release_pipeline.status,
                "rollback_state": release_pipeline.rollback_state,
            },
        )
    )

    dr_status: ReviewChecklistStatus
    if disaster_recovery_result.status == "completed":
        dr_status = "pass"
    elif disaster_recovery_result.status == "degraded":
        dr_status = "warn"
    else:
        dr_status = "fail"

    items.append(
        LaunchReadinessChecklistItem(
            item_id="disaster-recovery",
            title="Disaster Recovery",
            status=dr_status,
            detail=f"Disaster recovery drill status is {disaster_recovery_result.status}.",
            metadata={
                "drill_id": disaster_recovery_result.drill_id,
                "windows_breached_count": disaster_recovery_result.windows_breached_count,
                "failed_steps": disaster_recovery_result.failed_steps,
            },
        )
    )

    failure_status: ReviewChecklistStatus
    if failure_injection_report.readiness_score >= 0.9 and failure_injection_report.failed_count == 0:
        failure_status = "pass"
    elif failure_injection_report.readiness_score >= 0.75:
        failure_status = "warn"
    else:
        failure_status = "fail"

    items.append(
        LaunchReadinessChecklistItem(
            item_id="failure-injection",
            title="Failure Injection Drills",
            status=failure_status,
            detail=(
                "Failure injection readiness score is "
                f"{failure_injection_report.readiness_score:.2f}."
            ),
            metadata={
                "report_id": failure_injection_report.report_id,
                "readiness_score": failure_injection_report.readiness_score,
                "failed_count": failure_injection_report.failed_count,
            },
        )
    )

    required_playbooks = {
        "incident.prompt_injection",
        "incident.secret_exposure",
        "incident.policy_anomaly",
    }
    covered_playbooks = set(operator_runbook_bundle.incident_playbook_ids)
    missing_playbooks = sorted(required_playbooks - covered_playbooks)

    if missing_playbooks:
        runbook_status: ReviewChecklistStatus = "fail"
        detail = "Operator runbook bundle missing required incident playbooks: " + ", ".join(missing_playbooks)
    elif len(operator_runbook_bundle.documents) < 3:
        runbook_status = "warn"
        detail = "Operator runbook bundle has limited document coverage."
    else:
        runbook_status = "pass"
        detail = "Operator runbook bundle covers required incident playbooks."

    items.append(
        LaunchReadinessChecklistItem(
            item_id="operator-runbooks",
            title="Operator Runbooks",
            status=runbook_status,
            detail=detail,
            metadata={
                "bundle_id": operator_runbook_bundle.bundle_id,
                "document_count": len(operator_runbook_bundle.documents),
            },
        )
    )

    return tuple(sorted(items, key=lambda item: item.item_id))


def _derive_recommendation(
    checklist: tuple[LaunchReadinessChecklistItem, ...],
) -> LaunchReadinessRecommendation:
    statuses = {item.status for item in checklist}
    if "fail" in statuses:
        return "reject"
    if "warn" in statuses:
        return "hold"
    return "approve"


def _required_signoff_roles(
    recommendation: LaunchReadinessRecommendation,
) -> tuple[str, ...]:
    if recommendation == "approve":
        return ("primary_user", "authorized_operator")
    return ("primary_user",)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise LaunchReadinessReviewError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "ReviewChecklistStatus",
    "LaunchReadinessRecommendation",
    "LaunchReadinessReviewStatus",
    "LaunchReadinessDecision",
    "LaunchReadinessChecklistItem",
    "LaunchReadinessSignoff",
    "LaunchReadinessReview",
    "LaunchReadinessReviewError",
    "LaunchReadinessReviewWorkflow",
]
