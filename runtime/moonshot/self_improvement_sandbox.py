"""Self-improvement experiment sandbox with strict isolation controls."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from .benchmark_taxonomy import BenchmarkTaxonomy, build_default_benchmark_taxonomy, validate_benchmark_taxonomy

SandboxRunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]

_ALLOWED_RISK_TIERS = {"low", "medium", "high", "critical"}
_ALLOWED_FILESYSTEM_SCOPES = {"ephemeral_only"}
_ALLOWED_NETWORK_POLICIES = {"deny_all"}
_ALLOWED_SECRET_POLICIES = {"deny_all"}
_ALLOWED_TERMINAL_OUTCOMES = {"completed", "failed"}


@dataclass(frozen=True)
class SelfImprovementIsolationProfile:
    profile_id: str
    policy_version: str
    filesystem_scope: str
    network_policy: str
    secret_policy: str
    allowed_tool_ids: tuple[str, ...]
    required_controls: tuple[str, ...]
    max_runtime_seconds: int
    max_memory_mb: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SelfImprovementExperimentProposal:
    experiment_id: str
    title: str
    hypothesis: str
    target_capability_ids: tuple[str, ...]
    proposed_tool_ids: tuple[str, ...]
    risk_tier: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SelfImprovementSandboxEvent:
    sequence: int
    event_type: str
    occurred_at: str
    details: dict[str, Any]


@dataclass(frozen=True)
class SelfImprovementRunRecord:
    run_id: str
    experiment_id: str
    status: SandboxRunStatus
    isolation_profile_id: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    deterministic_seed: int
    tool_ids: tuple[str, ...]
    network_access: bool
    secret_access: bool
    filesystem_scope: str
    environment_fingerprint: str
    active_token: str | None
    artifact_digest: str | None
    error: str | None
    events: tuple[SelfImprovementSandboxEvent, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "status": self.status,
            "isolation_profile_id": self.isolation_profile_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "deterministic_seed": self.deterministic_seed,
            "tool_ids": list(self.tool_ids),
            "network_access": self.network_access,
            "secret_access": self.secret_access,
            "filesystem_scope": self.filesystem_scope,
            "environment_fingerprint": self.environment_fingerprint,
            "active_token": self.active_token,
            "artifact_digest": self.artifact_digest,
            "error": self.error,
            "events": [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "occurred_at": event.occurred_at,
                    "details": dict(event.details),
                }
                for event in self.events
            ],
            "metadata": dict(self.metadata),
        }


class SelfImprovementSandboxError(ValueError):
    """Raised when sandbox isolation or lifecycle constraints are violated."""


class SelfImprovementSandbox:
    """Registers and executes self-improvement experiments under strict isolation."""

    def __init__(
        self,
        taxonomy: BenchmarkTaxonomy | None = None,
        *,
        isolation_profile: SelfImprovementIsolationProfile | None = None,
    ) -> None:
        if taxonomy is None:
            taxonomy = build_default_benchmark_taxonomy()
        validate_benchmark_taxonomy(taxonomy)

        if isolation_profile is None:
            isolation_profile = build_default_self_improvement_isolation_profile()
        validate_self_improvement_isolation_profile(isolation_profile)

        self.taxonomy = taxonomy
        self.isolation_profile = isolation_profile

        self._capabilities_by_id = {
            capability.capability_id: capability
            for capability in taxonomy.capabilities
        }
        self._experiments: dict[str, SelfImprovementExperimentProposal] = {}
        self._runs: dict[str, SelfImprovementRunRecord] = {}
        self._consumed_tokens: set[str] = set()

    def register_experiment(
        self,
        proposal: SelfImprovementExperimentProposal,
    ) -> SelfImprovementExperimentProposal:
        if not isinstance(proposal, SelfImprovementExperimentProposal):
            raise TypeError("proposal must be SelfImprovementExperimentProposal")

        normalized = _normalize_proposal(
            proposal,
            capabilities_by_id=self._capabilities_by_id,
            profile=self.isolation_profile,
        )
        if normalized.experiment_id in self._experiments:
            raise SelfImprovementSandboxError(
                f"Duplicate experiment_id: {normalized.experiment_id}"
            )

        self._experiments[normalized.experiment_id] = normalized
        return normalized

    def get_experiment(self, experiment_id: str) -> SelfImprovementExperimentProposal:
        normalized_experiment_id = _normalize_required(experiment_id, "experiment_id").lower()
        proposal = self._experiments.get(normalized_experiment_id)
        if proposal is None:
            raise KeyError(f"Unknown experiment: {normalized_experiment_id}")
        return proposal

    def list_experiments(self) -> list[SelfImprovementExperimentProposal]:
        return sorted(self._experiments.values(), key=lambda item: item.experiment_id)

    def create_run(
        self,
        experiment_id: str,
        *,
        deterministic_seed: int,
        requested_tool_ids: list[str] | tuple[str, ...],
        network_access: bool = False,
        secret_access: bool = False,
        filesystem_scope: str = "ephemeral_only",
        metadata: dict[str, Any] | None = None,
    ) -> SelfImprovementRunRecord:
        proposal = self.get_experiment(experiment_id)

        normalized_seed = _normalize_seed(deterministic_seed)
        normalized_tool_ids = _normalize_identifier_tuple(
            requested_tool_ids,
            field_name="requested_tool_ids",
        )
        normalized_filesystem_scope = _normalize_required(filesystem_scope, "filesystem_scope").lower()
        if normalized_filesystem_scope not in _ALLOWED_FILESYSTEM_SCOPES:
            raise SelfImprovementSandboxError(
                f"Unsupported filesystem_scope: {normalized_filesystem_scope}"
            )

        self._validate_run_isolation_request(
            proposal=proposal,
            requested_tool_ids=normalized_tool_ids,
            network_access=bool(network_access),
            secret_access=bool(secret_access),
            filesystem_scope=normalized_filesystem_scope,
        )

        run_index = len(self._runs) + 1
        run_id = f"sandbox-run-{run_index:04d}-{proposal.experiment_id}"
        created_at = _utc_now_iso()
        initial_token = _build_transition_token(
            run_id=run_id,
            stage="pending",
            deterministic_seed=normalized_seed,
            tool_ids=normalized_tool_ids,
            sequence=1,
        )
        environment_fingerprint = _build_environment_fingerprint(
            profile=self.isolation_profile,
            tool_ids=normalized_tool_ids,
            deterministic_seed=normalized_seed,
        )

        created_event = SelfImprovementSandboxEvent(
            sequence=1,
            event_type="run_created",
            occurred_at=created_at,
            details={
                "experiment_id": proposal.experiment_id,
                "risk_tier": proposal.risk_tier,
                "tool_ids": list(normalized_tool_ids),
                "network_access": bool(network_access),
                "secret_access": bool(secret_access),
                "filesystem_scope": normalized_filesystem_scope,
                "required_controls": list(self.isolation_profile.required_controls),
            },
        )

        run_record = SelfImprovementRunRecord(
            run_id=run_id,
            experiment_id=proposal.experiment_id,
            status="pending",
            isolation_profile_id=self.isolation_profile.profile_id,
            created_at=created_at,
            started_at=None,
            completed_at=None,
            deterministic_seed=normalized_seed,
            tool_ids=normalized_tool_ids,
            network_access=bool(network_access),
            secret_access=bool(secret_access),
            filesystem_scope=normalized_filesystem_scope,
            environment_fingerprint=environment_fingerprint,
            active_token=initial_token,
            artifact_digest=None,
            error=None,
            events=(created_event,),
            metadata=dict(metadata or {}),
        )
        self._runs[run_id] = run_record
        return run_record

    def start_run(
        self,
        run_id: str,
        *,
        transition_token: str,
    ) -> SelfImprovementRunRecord:
        run = self.get_run(run_id)
        if run.status != "pending":
            raise SelfImprovementSandboxError(
                f"Run {run.run_id} is {run.status}; only pending runs can be started"
            )

        self._verify_active_token(run, transition_token=transition_token)
        self._consume_token(transition_token)

        started_at = _utc_now_iso()
        start_event = SelfImprovementSandboxEvent(
            sequence=len(run.events) + 1,
            event_type="run_started",
            occurred_at=started_at,
            details={
                "environment_fingerprint": run.environment_fingerprint,
                "tool_ids": list(run.tool_ids),
            },
        )

        rotated_token = _build_transition_token(
            run_id=run.run_id,
            stage="running",
            deterministic_seed=run.deterministic_seed,
            tool_ids=run.tool_ids,
            sequence=start_event.sequence,
        )

        updated = replace(
            run,
            status="running",
            started_at=started_at,
            active_token=rotated_token,
            events=run.events + (start_event,),
        )
        self._runs[run.run_id] = updated
        return updated

    def complete_run(
        self,
        run_id: str,
        *,
        transition_token: str,
        outcome: SandboxRunStatus,
        artifacts: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> SelfImprovementRunRecord:
        run = self.get_run(run_id)
        if run.status != "running":
            raise SelfImprovementSandboxError(
                f"Run {run.run_id} is {run.status}; only running runs can be completed"
            )

        normalized_outcome = _normalize_required(outcome, "outcome").lower()
        if normalized_outcome not in _ALLOWED_TERMINAL_OUTCOMES:
            allowed = ", ".join(sorted(_ALLOWED_TERMINAL_OUTCOMES))
            raise SelfImprovementSandboxError(
                f"Unsupported completion outcome {normalized_outcome}. Allowed: {allowed}"
            )

        self._verify_active_token(run, transition_token=transition_token)
        self._consume_token(transition_token)

        canonical_artifacts = dict(artifacts or {})
        artifact_digest = _build_artifact_digest(
            experiment_id=run.experiment_id,
            deterministic_seed=run.deterministic_seed,
            tool_ids=run.tool_ids,
            artifacts=canonical_artifacts,
        )

        if normalized_outcome == "completed" and error is not None:
            raise SelfImprovementSandboxError(
                "error must be omitted when outcome is completed"
            )

        normalized_error = None
        if normalized_outcome == "failed":
            if error is None:
                normalized_error = "run_failed_without_error"
            else:
                normalized_error = _normalize_required(error, "error")

        completed_at = _utc_now_iso()
        completion_event = SelfImprovementSandboxEvent(
            sequence=len(run.events) + 1,
            event_type=("run_completed" if normalized_outcome == "completed" else "run_failed"),
            occurred_at=completed_at,
            details={
                "artifact_digest": artifact_digest,
                "artifact_keys": sorted(canonical_artifacts),
                "error": normalized_error,
            },
        )

        updated = replace(
            run,
            status=normalized_outcome,
            completed_at=completed_at,
            active_token=None,
            artifact_digest=artifact_digest,
            error=normalized_error,
            events=run.events + (completion_event,),
        )
        self._runs[run.run_id] = updated
        return updated

    def cancel_run(
        self,
        run_id: str,
        *,
        transition_token: str,
        reason: str,
    ) -> SelfImprovementRunRecord:
        run = self.get_run(run_id)
        if run.status in {"completed", "failed", "cancelled"}:
            raise SelfImprovementSandboxError(
                f"Run {run.run_id} is already terminal with status {run.status}"
            )

        self._verify_active_token(run, transition_token=transition_token)
        self._consume_token(transition_token)

        normalized_reason = _normalize_required(reason, "reason")
        cancelled_at = _utc_now_iso()
        cancel_event = SelfImprovementSandboxEvent(
            sequence=len(run.events) + 1,
            event_type="run_cancelled",
            occurred_at=cancelled_at,
            details={"reason": normalized_reason},
        )

        updated = replace(
            run,
            status="cancelled",
            completed_at=cancelled_at,
            active_token=None,
            error=normalized_reason,
            events=run.events + (cancel_event,),
        )
        self._runs[run.run_id] = updated
        return updated

    def get_run(self, run_id: str) -> SelfImprovementRunRecord:
        normalized_run_id = _normalize_required(run_id, "run_id").lower()
        run = self._runs.get(normalized_run_id)
        if run is None:
            raise KeyError(f"Unknown sandbox run: {normalized_run_id}")
        return run

    def list_runs(
        self,
        *,
        status: SandboxRunStatus | None = None,
        experiment_id: str | None = None,
    ) -> list[SelfImprovementRunRecord]:
        runs = list(self._runs.values())

        if status is not None:
            normalized_status = _normalize_required(status, "status").lower()
            runs = [item for item in runs if item.status == normalized_status]

        if experiment_id is not None:
            normalized_experiment_id = _normalize_required(experiment_id, "experiment_id").lower()
            runs = [item for item in runs if item.experiment_id == normalized_experiment_id]

        return sorted(runs, key=lambda item: item.run_id)

    def _validate_run_isolation_request(
        self,
        *,
        proposal: SelfImprovementExperimentProposal,
        requested_tool_ids: tuple[str, ...],
        network_access: bool,
        secret_access: bool,
        filesystem_scope: str,
    ) -> None:
        if not requested_tool_ids:
            raise SelfImprovementSandboxError("requested_tool_ids must include at least one tool")

        profile_tools = set(self.isolation_profile.allowed_tool_ids)
        proposal_tools = set(proposal.proposed_tool_ids)

        for tool_id in requested_tool_ids:
            if tool_id not in profile_tools:
                raise SelfImprovementSandboxError(
                    f"Tool {tool_id} is not permitted by isolation profile {self.isolation_profile.profile_id}"
                )
            if tool_id not in proposal_tools:
                raise SelfImprovementSandboxError(
                    f"Tool {tool_id} is not declared in experiment {proposal.experiment_id}"
                )

        if filesystem_scope != self.isolation_profile.filesystem_scope:
            raise SelfImprovementSandboxError(
                f"filesystem_scope {filesystem_scope} violates strict profile scope {self.isolation_profile.filesystem_scope}"
            )

        if network_access and self.isolation_profile.network_policy == "deny_all":
            raise SelfImprovementSandboxError(
                "Network access is denied by strict isolation profile"
            )

        if secret_access and self.isolation_profile.secret_policy == "deny_all":
            raise SelfImprovementSandboxError(
                "Secret access is denied by strict isolation profile"
            )

    def _verify_active_token(self, run: SelfImprovementRunRecord, *, transition_token: str) -> None:
        normalized_transition_token = _normalize_required(transition_token, "transition_token")

        expected_token = run.active_token
        if expected_token is None:
            raise SelfImprovementSandboxError(f"Run {run.run_id} has no active transition token")

        if normalized_transition_token in self._consumed_tokens:
            raise SelfImprovementSandboxError("Transition token has already been consumed")

        if normalized_transition_token != expected_token:
            raise SelfImprovementSandboxError("Transition token does not match active run token")

    def _consume_token(self, transition_token: str) -> None:
        normalized_transition_token = _normalize_required(transition_token, "transition_token")
        if normalized_transition_token in self._consumed_tokens:
            raise SelfImprovementSandboxError("Transition token has already been consumed")
        self._consumed_tokens.add(normalized_transition_token)


def build_default_self_improvement_isolation_profile() -> SelfImprovementIsolationProfile:
    profile = SelfImprovementIsolationProfile(
        profile_id="strict_isolation",
        policy_version="1.0.0",
        filesystem_scope="ephemeral_only",
        network_policy="deny_all",
        secret_policy="deny_all",
        allowed_tool_ids=(
            "benchmark_harness",
            "result_reporter",
            "scenario_builder",
            "taxonomy_manifest",
        ),
        required_controls=(
            "ephemeral_filesystem",
            "network_isolation",
            "secret_isolation",
            "tool_allowlist",
            "deterministic_seed_required",
            "single_use_transition_tokens",
            "artifact_digest_required",
        ),
        max_runtime_seconds=1800,
        max_memory_mb=2048,
        metadata={
            "program": "moonshot_capability",
            "phase": "P10-T5",
            "notes": "Strict sandbox profile blocks network and secret access by default.",
        },
    )
    validate_self_improvement_isolation_profile(profile)
    return profile


def validate_self_improvement_isolation_profile(profile: SelfImprovementIsolationProfile) -> None:
    if not isinstance(profile, SelfImprovementIsolationProfile):
        raise TypeError("profile must be SelfImprovementIsolationProfile")

    _normalize_required(profile.profile_id, "profile_id")
    _normalize_required(profile.policy_version, "policy_version")

    normalized_filesystem_scope = _normalize_required(profile.filesystem_scope, "filesystem_scope").lower()
    if normalized_filesystem_scope not in _ALLOWED_FILESYSTEM_SCOPES:
        allowed = ", ".join(sorted(_ALLOWED_FILESYSTEM_SCOPES))
        raise SelfImprovementSandboxError(
            f"Unsupported filesystem_scope {normalized_filesystem_scope}. Allowed: {allowed}"
        )

    normalized_network_policy = _normalize_required(profile.network_policy, "network_policy").lower()
    if normalized_network_policy not in _ALLOWED_NETWORK_POLICIES:
        allowed = ", ".join(sorted(_ALLOWED_NETWORK_POLICIES))
        raise SelfImprovementSandboxError(
            f"Unsupported network_policy {normalized_network_policy}. Allowed: {allowed}"
        )

    normalized_secret_policy = _normalize_required(profile.secret_policy, "secret_policy").lower()
    if normalized_secret_policy not in _ALLOWED_SECRET_POLICIES:
        allowed = ", ".join(sorted(_ALLOWED_SECRET_POLICIES))
        raise SelfImprovementSandboxError(
            f"Unsupported secret_policy {normalized_secret_policy}. Allowed: {allowed}"
        )

    if profile.max_runtime_seconds < 60:
        raise SelfImprovementSandboxError("max_runtime_seconds must be at least 60")
    if profile.max_memory_mb < 128:
        raise SelfImprovementSandboxError("max_memory_mb must be at least 128")

    if not profile.allowed_tool_ids:
        raise SelfImprovementSandboxError("allowed_tool_ids must include at least one tool")
    _normalize_identifier_tuple(profile.allowed_tool_ids, field_name="allowed_tool_ids")

    if not profile.required_controls:
        raise SelfImprovementSandboxError("required_controls must include at least one control")

    required_controls = _normalize_identifier_tuple(
        profile.required_controls,
        field_name="required_controls",
    )
    for expected_control in (
        "single_use_transition_tokens",
        "network_isolation",
        "secret_isolation",
        "ephemeral_filesystem",
    ):
        if expected_control not in required_controls:
            raise SelfImprovementSandboxError(
                f"required_controls must include {expected_control}"
            )


def _normalize_proposal(
    proposal: SelfImprovementExperimentProposal,
    *,
    capabilities_by_id: dict[str, Any],
    profile: SelfImprovementIsolationProfile,
) -> SelfImprovementExperimentProposal:
    experiment_id = _normalize_required(proposal.experiment_id, "experiment_id").lower()
    title = _normalize_required(proposal.title, "title")
    hypothesis = _normalize_required(proposal.hypothesis, "hypothesis")

    target_capability_ids = _normalize_identifier_tuple(
        proposal.target_capability_ids,
        field_name="target_capability_ids",
    )
    if not target_capability_ids:
        raise SelfImprovementSandboxError("target_capability_ids must include at least one capability")
    for capability_id in target_capability_ids:
        if capability_id not in capabilities_by_id:
            raise SelfImprovementSandboxError(
                f"Experiment {experiment_id} references unknown capability {capability_id}"
            )

    proposed_tool_ids = _normalize_identifier_tuple(
        proposal.proposed_tool_ids,
        field_name="proposed_tool_ids",
    )
    if not proposed_tool_ids:
        raise SelfImprovementSandboxError("proposed_tool_ids must include at least one tool")

    allowed_tool_ids = set(profile.allowed_tool_ids)
    for tool_id in proposed_tool_ids:
        if tool_id not in allowed_tool_ids:
            raise SelfImprovementSandboxError(
                f"Experiment {experiment_id} requested unsupported tool {tool_id}"
            )

    risk_tier = _normalize_required(proposal.risk_tier, "risk_tier").lower()
    if risk_tier not in _ALLOWED_RISK_TIERS:
        allowed = ", ".join(sorted(_ALLOWED_RISK_TIERS))
        raise SelfImprovementSandboxError(
            f"Unsupported risk_tier {risk_tier}. Allowed: {allowed}"
        )

    return SelfImprovementExperimentProposal(
        experiment_id=experiment_id,
        title=title,
        hypothesis=hypothesis,
        target_capability_ids=target_capability_ids,
        proposed_tool_ids=proposed_tool_ids,
        risk_tier=risk_tier,
        metadata=dict(proposal.metadata),
    )


def _normalize_identifier_tuple(
    values: list[str] | tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        item = _normalize_required(value, field_name).lower()
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)

    return tuple(sorted(normalized))


def _normalize_seed(seed: int) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("deterministic_seed must be an integer")
    if seed < 0:
        raise SelfImprovementSandboxError("deterministic_seed must be non-negative")
    return int(seed)


def _build_environment_fingerprint(
    *,
    profile: SelfImprovementIsolationProfile,
    tool_ids: tuple[str, ...],
    deterministic_seed: int,
) -> str:
    canonical = json.dumps(
        {
            "profile_id": profile.profile_id,
            "policy_version": profile.policy_version,
            "filesystem_scope": profile.filesystem_scope,
            "network_policy": profile.network_policy,
            "secret_policy": profile.secret_policy,
            "tool_ids": list(tool_ids),
            "deterministic_seed": deterministic_seed,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"env-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _build_transition_token(
    *,
    run_id: str,
    stage: str,
    deterministic_seed: int,
    tool_ids: tuple[str, ...],
    sequence: int,
) -> str:
    canonical = json.dumps(
        {
            "run_id": run_id,
            "stage": stage,
            "deterministic_seed": deterministic_seed,
            "tool_ids": list(tool_ids),
            "sequence": sequence,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"sbx-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _build_artifact_digest(
    *,
    experiment_id: str,
    deterministic_seed: int,
    tool_ids: tuple[str, ...],
    artifacts: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "experiment_id": experiment_id,
            "deterministic_seed": deterministic_seed,
            "tool_ids": list(tool_ids),
            "artifacts": artifacts,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"artifact-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise SelfImprovementSandboxError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
