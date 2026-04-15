"""Experiment approval and rollback controls for moonshot sandbox runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from .self_improvement_sandbox import SelfImprovementRunRecord, SelfImprovementSandbox

RiskTier = Literal["low", "medium", "high", "critical"]
ReviewerRole = Literal["primary_user", "authorized_operator", "system", "limited_user"]
ApprovalRequestStatus = Literal["pending", "approved", "rejected", "promoted"]
RollbackState = Literal["available", "executed"]

_ALLOWED_RISK_TIERS = {"low", "medium", "high", "critical"}
_ALLOWED_REVIEWER_ROLES = {"primary_user", "authorized_operator", "system", "limited_user"}
_ALLOWED_APPROVAL_ROLES = {"primary_user", "authorized_operator", "system"}


@dataclass(frozen=True)
class ExperimentRiskApprovalRule:
    risk_tier: str
    min_approvals: int
    required_reviewer_roles: tuple[str, ...]
    allowed_reviewer_roles: tuple[str, ...]
    allow_requester_approval: bool
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ExperimentApprovalPolicy:
    policy_id: str
    policy_version: str
    rules: tuple[ExperimentRiskApprovalRule, ...]
    metadata: dict[str, Any]

    def get_rule(self, risk_tier: str) -> ExperimentRiskApprovalRule:
        normalized_risk_tier = _normalize_required(risk_tier, "risk_tier").lower()
        for rule in self.rules:
            if rule.risk_tier == normalized_risk_tier:
                return rule
        raise KeyError(f"Unknown risk tier in policy: {normalized_risk_tier}")


@dataclass(frozen=True)
class ExperimentApprovalRecord:
    approval_id: str
    approver_id: str
    approver_role: str
    approved_at: str
    note: str | None


@dataclass(frozen=True)
class ExperimentApprovalRequest:
    request_id: str
    run_id: str
    experiment_id: str
    risk_tier: str
    requested_by: str
    summary: str
    status: ApprovalRequestStatus
    created_at: str
    required_approvals: int
    required_reviewer_roles: tuple[str, ...]
    allowed_reviewer_roles: tuple[str, ...]
    allow_requester_approval: bool
    approvals: tuple[ExperimentApprovalRecord, ...]
    active_transition_token: str | None
    rejection_reason: str | None
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "risk_tier": self.risk_tier,
            "requested_by": self.requested_by,
            "summary": self.summary,
            "status": self.status,
            "created_at": self.created_at,
            "required_approvals": self.required_approvals,
            "required_reviewer_roles": list(self.required_reviewer_roles),
            "allowed_reviewer_roles": list(self.allowed_reviewer_roles),
            "allow_requester_approval": self.allow_requester_approval,
            "approvals": [
                {
                    "approval_id": approval.approval_id,
                    "approver_id": approval.approver_id,
                    "approver_role": approval.approver_role,
                    "approved_at": approval.approved_at,
                    "note": approval.note,
                }
                for approval in self.approvals
            ],
            "active_transition_token": self.active_transition_token,
            "rejection_reason": self.rejection_reason,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ExperimentPromotionRecord:
    promotion_id: str
    request_id: str
    run_id: str
    experiment_id: str
    target_id: str
    previous_version: str
    promoted_version: str
    promoted_at: str
    rollback_state: RollbackState
    rollback_token: str
    rolled_back_at: str | None
    rollback_reason: str | None
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "promotion_id": self.promotion_id,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "target_id": self.target_id,
            "previous_version": self.previous_version,
            "promoted_version": self.promoted_version,
            "promoted_at": self.promoted_at,
            "rollback_state": self.rollback_state,
            "rollback_token": self.rollback_token,
            "rolled_back_at": self.rolled_back_at,
            "rollback_reason": self.rollback_reason,
            "metadata": dict(self.metadata),
        }


class ExperimentApprovalControlError(ValueError):
    """Raised when approval and rollback control checks fail."""


class ExperimentApprovalController:
    """Applies approval and rollback controls to self-improvement sandbox runs."""

    def __init__(
        self,
        sandbox: SelfImprovementSandbox,
        *,
        policy: ExperimentApprovalPolicy | None = None,
    ) -> None:
        if not isinstance(sandbox, SelfImprovementSandbox):
            raise TypeError("sandbox must be SelfImprovementSandbox")

        if policy is None:
            policy = build_default_experiment_approval_policy()
        validate_experiment_approval_policy(policy)

        self.sandbox = sandbox
        self.policy = policy

        self._requests: dict[str, ExperimentApprovalRequest] = {}
        self._promotions: dict[str, ExperimentPromotionRecord] = {}
        self._run_to_request: dict[str, str] = {}
        self._request_to_promotion: dict[str, str] = {}
        self._consumed_transition_tokens: set[str] = set()
        self._consumed_rollback_tokens: set[str] = set()

    def create_approval_request(
        self,
        run_id: str,
        *,
        requested_by: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> ExperimentApprovalRequest:
        run = self._get_completed_run(run_id)

        if run.run_id in self._run_to_request:
            raise ExperimentApprovalControlError(
                f"Run {run.run_id} already has an approval request"
            )

        proposal = self.sandbox.get_experiment(run.experiment_id)
        rule = self.policy.get_rule(proposal.risk_tier)

        request_index = len(self._requests) + 1
        request_id = f"approval-{request_index:04d}-{run.experiment_id}"
        created_at = _utc_now_iso()
        initial_token = _build_transition_token(
            request_id=request_id,
            run_id=run.run_id,
            sequence=1,
            stage="pending",
        )

        request = ExperimentApprovalRequest(
            request_id=request_id,
            run_id=run.run_id,
            experiment_id=run.experiment_id,
            risk_tier=proposal.risk_tier,
            requested_by=_normalize_required(requested_by, "requested_by"),
            summary=_normalize_required(summary, "summary"),
            status="pending",
            created_at=created_at,
            required_approvals=rule.min_approvals,
            required_reviewer_roles=rule.required_reviewer_roles,
            allowed_reviewer_roles=rule.allowed_reviewer_roles,
            allow_requester_approval=rule.allow_requester_approval,
            approvals=(),
            active_transition_token=initial_token,
            rejection_reason=None,
            metadata=dict(metadata or {}),
        )

        self._requests[request.request_id] = request
        self._run_to_request[run.run_id] = request.request_id
        return request

    def add_approval(
        self,
        request_id: str,
        *,
        approver_id: str,
        approver_role: ReviewerRole | str,
        note: str | None = None,
    ) -> ExperimentApprovalRequest:
        request = self.get_request(request_id)
        if request.status not in {"pending", "approved"}:
            raise ExperimentApprovalControlError(
                f"Approval request {request.request_id} is {request.status}; cannot add approvals"
            )

        normalized_approver_id = _normalize_required(approver_id, "approver_id")
        normalized_role = _normalize_required(approver_role, "approver_role").lower()

        if normalized_role not in _ALLOWED_REVIEWER_ROLES:
            allowed = ", ".join(sorted(_ALLOWED_REVIEWER_ROLES))
            raise ExperimentApprovalControlError(
                f"Unsupported approver_role {normalized_role}. Allowed: {allowed}"
            )

        if normalized_role not in request.allowed_reviewer_roles:
            raise ExperimentApprovalControlError(
                f"Role {normalized_role} is not allowed for risk tier {request.risk_tier}"
            )

        if normalized_role not in _ALLOWED_APPROVAL_ROLES:
            raise ExperimentApprovalControlError(
                f"Role {normalized_role} cannot approve experiment promotions"
            )

        if not request.allow_requester_approval and normalized_approver_id == request.requested_by:
            raise ExperimentApprovalControlError(
                f"Requester {request.requested_by} cannot approve this request"
            )

        for approval in request.approvals:
            if approval.approver_id == normalized_approver_id:
                raise ExperimentApprovalControlError(
                    f"Approver {normalized_approver_id} already approved request {request.request_id}"
                )

        approval_record = ExperimentApprovalRecord(
            approval_id=f"{request.request_id}-appr-{len(request.approvals) + 1:02d}",
            approver_id=normalized_approver_id,
            approver_role=normalized_role,
            approved_at=_utc_now_iso(),
            note=_normalize_optional(note),
        )

        updated_approvals = request.approvals + (approval_record,)
        new_status: ApprovalRequestStatus = (
            "approved" if _has_sufficient_approvals(request, updated_approvals) else "pending"
        )
        next_token = _build_transition_token(
            request_id=request.request_id,
            run_id=request.run_id,
            sequence=len(updated_approvals) + 1,
            stage=new_status,
        )

        updated_request = replace(
            request,
            status=new_status,
            approvals=updated_approvals,
            active_transition_token=next_token,
        )
        self._requests[updated_request.request_id] = updated_request
        return updated_request

    def reject_request(
        self,
        request_id: str,
        *,
        reviewer_id: str,
        reviewer_role: ReviewerRole | str,
        reason: str,
    ) -> ExperimentApprovalRequest:
        request = self.get_request(request_id)
        if request.status == "promoted":
            raise ExperimentApprovalControlError(
                f"Approval request {request.request_id} is already promoted"
            )

        normalized_reviewer_id = _normalize_required(reviewer_id, "reviewer_id")
        normalized_reviewer_role = _normalize_required(reviewer_role, "reviewer_role").lower()
        normalized_reason = _normalize_required(reason, "reason")

        if normalized_reviewer_role not in _ALLOWED_APPROVAL_ROLES:
            raise ExperimentApprovalControlError(
                f"Role {normalized_reviewer_role} cannot reject experiment promotions"
            )

        if normalized_reviewer_role not in request.allowed_reviewer_roles:
            raise ExperimentApprovalControlError(
                f"Role {normalized_reviewer_role} is not allowed for risk tier {request.risk_tier}"
            )

        updated_request = replace(
            request,
            status="rejected",
            active_transition_token=None,
            rejection_reason=f"{normalized_reviewer_id}:{normalized_reason}",
        )
        self._requests[updated_request.request_id] = updated_request
        return updated_request

    def promote_request(
        self,
        request_id: str,
        *,
        transition_token: str,
        target_id: str,
        previous_version: str,
        promoted_version: str,
        metadata: dict[str, Any] | None = None,
    ) -> ExperimentPromotionRecord:
        request = self.get_request(request_id)
        if request.status != "approved":
            raise ExperimentApprovalControlError(
                f"Approval request {request.request_id} is {request.status}; only approved requests can be promoted"
            )

        if request.request_id in self._request_to_promotion:
            raise ExperimentApprovalControlError(
                f"Approval request {request.request_id} already has a promotion record"
            )

        self._verify_transition_token(request, transition_token=transition_token)
        self._consume_transition_token(transition_token)

        run = self._get_completed_run(request.run_id)
        if run.artifact_digest is None:
            raise ExperimentApprovalControlError(
                f"Run {run.run_id} cannot be promoted without artifact digest"
            )

        promotion_index = len(self._promotions) + 1
        promotion_id = f"promotion-{promotion_index:04d}-{request.experiment_id}"
        normalized_target_id = _normalize_required(target_id, "target_id")
        normalized_previous_version = _normalize_required(previous_version, "previous_version")
        normalized_promoted_version = _normalize_required(promoted_version, "promoted_version")

        rollback_token = _build_rollback_token(
            promotion_id=promotion_id,
            experiment_id=request.experiment_id,
            promoted_version=normalized_promoted_version,
            artifact_digest=run.artifact_digest,
        )

        promotion = ExperimentPromotionRecord(
            promotion_id=promotion_id,
            request_id=request.request_id,
            run_id=request.run_id,
            experiment_id=request.experiment_id,
            target_id=normalized_target_id,
            previous_version=normalized_previous_version,
            promoted_version=normalized_promoted_version,
            promoted_at=_utc_now_iso(),
            rollback_state="available",
            rollback_token=rollback_token,
            rolled_back_at=None,
            rollback_reason=None,
            metadata=dict(metadata or {}),
        )

        self._promotions[promotion.promotion_id] = promotion
        self._request_to_promotion[request.request_id] = promotion.promotion_id
        self._requests[request.request_id] = replace(
            request,
            status="promoted",
            active_transition_token=None,
        )
        return promotion

    def execute_rollback(
        self,
        promotion_id: str,
        *,
        rollback_token: str,
        actor_role: ReviewerRole | str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> ExperimentPromotionRecord:
        promotion = self.get_promotion(promotion_id)
        if promotion.rollback_state != "available":
            raise ExperimentApprovalControlError(
                f"Promotion {promotion.promotion_id} rollback is {promotion.rollback_state}"
            )

        normalized_actor_role = _normalize_required(actor_role, "actor_role").lower()
        if normalized_actor_role not in _ALLOWED_APPROVAL_ROLES:
            raise ExperimentApprovalControlError(
                f"Role {normalized_actor_role} cannot execute rollback"
            )

        normalized_rollback_token = _normalize_required(rollback_token, "rollback_token")
        if normalized_rollback_token != promotion.rollback_token:
            raise ExperimentApprovalControlError("rollback_token does not match promotion record")
        if normalized_rollback_token in self._consumed_rollback_tokens:
            raise ExperimentApprovalControlError("rollback_token has already been consumed")

        normalized_reason = _normalize_required(reason, "reason")
        self._consumed_rollback_tokens.add(normalized_rollback_token)

        merged_metadata = dict(promotion.metadata)
        if metadata:
            merged_metadata.update(dict(metadata))
        merged_metadata["rollback_actor_role"] = normalized_actor_role

        updated = replace(
            promotion,
            rollback_state="executed",
            rolled_back_at=_utc_now_iso(),
            rollback_reason=normalized_reason,
            metadata=merged_metadata,
        )
        self._promotions[updated.promotion_id] = updated
        return updated

    def get_request(self, request_id: str) -> ExperimentApprovalRequest:
        normalized_request_id = _normalize_required(request_id, "request_id").lower()
        request = self._requests.get(normalized_request_id)
        if request is None:
            raise KeyError(f"Unknown approval request: {normalized_request_id}")
        return request

    def list_requests(
        self,
        *,
        status: ApprovalRequestStatus | None = None,
        experiment_id: str | None = None,
    ) -> list[ExperimentApprovalRequest]:
        requests = list(self._requests.values())

        if status is not None:
            normalized_status = _normalize_required(status, "status").lower()
            requests = [item for item in requests if item.status == normalized_status]

        if experiment_id is not None:
            normalized_experiment_id = _normalize_required(experiment_id, "experiment_id").lower()
            requests = [item for item in requests if item.experiment_id == normalized_experiment_id]

        return sorted(requests, key=lambda item: item.request_id)

    def get_promotion(self, promotion_id: str) -> ExperimentPromotionRecord:
        normalized_promotion_id = _normalize_required(promotion_id, "promotion_id").lower()
        promotion = self._promotions.get(normalized_promotion_id)
        if promotion is None:
            raise KeyError(f"Unknown promotion record: {normalized_promotion_id}")
        return promotion

    def list_promotions(
        self,
        *,
        experiment_id: str | None = None,
        rollback_state: RollbackState | None = None,
    ) -> list[ExperimentPromotionRecord]:
        promotions = list(self._promotions.values())

        if experiment_id is not None:
            normalized_experiment_id = _normalize_required(experiment_id, "experiment_id").lower()
            promotions = [item for item in promotions if item.experiment_id == normalized_experiment_id]

        if rollback_state is not None:
            normalized_rollback_state = _normalize_required(rollback_state, "rollback_state").lower()
            promotions = [item for item in promotions if item.rollback_state == normalized_rollback_state]

        return sorted(promotions, key=lambda item: item.promotion_id)

    def _get_completed_run(self, run_id: str) -> SelfImprovementRunRecord:
        run = self.sandbox.get_run(run_id)
        if run.status != "completed":
            raise ExperimentApprovalControlError(
                f"Run {run.run_id} has status {run.status}; only completed runs are eligible"
            )
        return run

    def _verify_transition_token(
        self,
        request: ExperimentApprovalRequest,
        *,
        transition_token: str,
    ) -> None:
        normalized_token = _normalize_required(transition_token, "transition_token")

        expected = request.active_transition_token
        if expected is None:
            raise ExperimentApprovalControlError(
                f"Request {request.request_id} has no active transition token"
            )
        if normalized_token != expected:
            raise ExperimentApprovalControlError("transition_token does not match request token")
        if normalized_token in self._consumed_transition_tokens:
            raise ExperimentApprovalControlError("transition_token has already been consumed")

    def _consume_transition_token(self, transition_token: str) -> None:
        normalized_token = _normalize_required(transition_token, "transition_token")
        if normalized_token in self._consumed_transition_tokens:
            raise ExperimentApprovalControlError("transition_token has already been consumed")
        self._consumed_transition_tokens.add(normalized_token)


def build_default_experiment_approval_policy() -> ExperimentApprovalPolicy:
    policy = ExperimentApprovalPolicy(
        policy_id="moonshot_experiment_approval",
        policy_version="1.0.0",
        rules=(
            ExperimentRiskApprovalRule(
                risk_tier="low",
                min_approvals=1,
                required_reviewer_roles=(),
                allowed_reviewer_roles=("primary_user", "authorized_operator", "system"),
                allow_requester_approval=True,
                metadata={"notes": "Low risk can be self-approved by an allowed reviewer role."},
            ),
            ExperimentRiskApprovalRule(
                risk_tier="medium",
                min_approvals=1,
                required_reviewer_roles=(),
                allowed_reviewer_roles=("primary_user", "authorized_operator", "system"),
                allow_requester_approval=True,
                metadata={"notes": "Medium risk requires one explicit reviewer approval."},
            ),
            ExperimentRiskApprovalRule(
                risk_tier="high",
                min_approvals=2,
                required_reviewer_roles=("primary_user",),
                allowed_reviewer_roles=("primary_user", "authorized_operator", "system"),
                allow_requester_approval=False,
                metadata={"notes": "High risk needs dual approval and at least one primary user reviewer."},
            ),
            ExperimentRiskApprovalRule(
                risk_tier="critical",
                min_approvals=2,
                required_reviewer_roles=("primary_user", "authorized_operator"),
                allowed_reviewer_roles=("primary_user", "authorized_operator", "system"),
                allow_requester_approval=False,
                metadata={"notes": "Critical risk needs both primary user and authorized operator approvals."},
            ),
        ),
        metadata={
            "program": "moonshot_capability",
            "phase": "P10-T6",
            "notes": "Approval policy for sandbox experiment promotions and rollback control.",
        },
    )
    validate_experiment_approval_policy(policy)
    return policy


def validate_experiment_approval_policy(policy: ExperimentApprovalPolicy) -> None:
    if not isinstance(policy, ExperimentApprovalPolicy):
        raise TypeError("policy must be ExperimentApprovalPolicy")

    _normalize_required(policy.policy_id, "policy_id")
    _normalize_required(policy.policy_version, "policy_version")

    if not policy.rules:
        raise ExperimentApprovalControlError("policy must include at least one rule")

    rules_by_risk_tier: dict[str, ExperimentRiskApprovalRule] = {}
    for rule in policy.rules:
        normalized_risk_tier = _normalize_required(rule.risk_tier, "risk_tier").lower()
        if normalized_risk_tier in rules_by_risk_tier:
            raise ExperimentApprovalControlError(
                f"Duplicate risk tier rule: {normalized_risk_tier}"
            )
        if normalized_risk_tier not in _ALLOWED_RISK_TIERS:
            allowed = ", ".join(sorted(_ALLOWED_RISK_TIERS))
            raise ExperimentApprovalControlError(
                f"Unsupported risk tier {normalized_risk_tier}. Allowed: {allowed}"
            )

        if rule.min_approvals < 1:
            raise ExperimentApprovalControlError(
                f"Rule {normalized_risk_tier} min_approvals must be at least 1"
            )

        required_roles = _normalize_identifier_tuple(
            rule.required_reviewer_roles,
            field_name=f"{normalized_risk_tier}.required_reviewer_roles",
            allowed_set=_ALLOWED_REVIEWER_ROLES,
        )
        allowed_roles = _normalize_identifier_tuple(
            rule.allowed_reviewer_roles,
            field_name=f"{normalized_risk_tier}.allowed_reviewer_roles",
            allowed_set=_ALLOWED_REVIEWER_ROLES,
        )

        if not allowed_roles:
            raise ExperimentApprovalControlError(
                f"Rule {normalized_risk_tier} must include at least one allowed reviewer role"
            )

        for required_role in required_roles:
            if required_role not in allowed_roles:
                raise ExperimentApprovalControlError(
                    f"Rule {normalized_risk_tier} required role {required_role} is not in allowed roles"
                )

        rules_by_risk_tier[normalized_risk_tier] = rule

    missing_risk_tiers = sorted(_ALLOWED_RISK_TIERS - set(rules_by_risk_tier))
    if missing_risk_tiers:
        raise ExperimentApprovalControlError(
            "policy missing risk tier rules: " + ", ".join(missing_risk_tiers)
        )


def _has_sufficient_approvals(
    request: ExperimentApprovalRequest,
    approvals: tuple[ExperimentApprovalRecord, ...],
) -> bool:
    if len(approvals) < request.required_approvals:
        return False

    approval_roles = {approval.approver_role for approval in approvals}
    for required_role in request.required_reviewer_roles:
        if required_role not in approval_roles:
            return False

    return True


def _normalize_identifier_tuple(
    values: tuple[str, ...] | list[str],
    *,
    field_name: str,
    allowed_set: set[str],
) -> tuple[str, ...]:
    normalized_values: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized_value = _normalize_required(value, field_name).lower()
        if normalized_value not in allowed_set:
            allowed = ", ".join(sorted(allowed_set))
            raise ExperimentApprovalControlError(
                f"{field_name} contains unsupported role {normalized_value}. Allowed: {allowed}"
            )
        if normalized_value in seen:
            continue
        seen.add(normalized_value)
        normalized_values.append(normalized_value)

    return tuple(sorted(normalized_values))


def _build_transition_token(
    *,
    request_id: str,
    run_id: str,
    sequence: int,
    stage: str,
) -> str:
    canonical = json.dumps(
        {
            "request_id": request_id,
            "run_id": run_id,
            "sequence": sequence,
            "stage": stage,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"apr-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _build_rollback_token(
    *,
    promotion_id: str,
    experiment_id: str,
    promoted_version: str,
    artifact_digest: str,
) -> str:
    canonical = json.dumps(
        {
            "promotion_id": promotion_id,
            "experiment_id": experiment_id,
            "promoted_version": promoted_version,
            "artifact_digest": artifact_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"rbk-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ExperimentApprovalControlError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
