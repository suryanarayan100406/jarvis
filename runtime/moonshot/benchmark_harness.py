"""Benchmark harness runner with reproducible scoring semantics."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Callable, Protocol
from uuid import uuid4

from .benchmark_taxonomy import (
    BenchmarkCapabilityDefinition,
    BenchmarkDifficultyBand,
    BenchmarkTaxonomy,
    validate_benchmark_taxonomy,
)


@dataclass(frozen=True)
class BenchmarkScenarioDefinition:
    scenario_id: str
    capability_id: str
    difficulty_band_id: str = "baseline"
    prompt: str | None = None
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkScenarioEvaluation:
    raw_score: float
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkScenarioResult:
    scenario_id: str
    capability_id: str
    domain_id: str
    difficulty_band_id: str
    scenario_seed: int
    raw_score: float
    normalized_score: float
    weight: float
    difficulty_multiplier: float
    weighted_score: float
    evidence: dict[str, Any]


@dataclass(frozen=True)
class BenchmarkCapabilityScore:
    capability_id: str
    domain_id: str
    scenario_count: int
    weighted_score: float
    weight: float


@dataclass(frozen=True)
class BenchmarkDomainScore:
    domain_id: str
    scenario_count: int
    capability_count: int
    weighted_score: float
    weight: float
    capability_scores: tuple[BenchmarkCapabilityScore, ...]


@dataclass(frozen=True)
class BenchmarkHarnessRunResult:
    run_id: str
    taxonomy_version: str
    scoring_version: str
    seed: int
    started_at: str
    completed_at: str
    strict_coverage: bool
    scenario_count: int
    scenario_results: tuple[BenchmarkScenarioResult, ...]
    domain_scores: tuple[BenchmarkDomainScore, ...]
    overall_score: float
    deterministic_digest: str
    metadata: dict[str, Any]

    def get_domain_score(self, domain_id: str) -> BenchmarkDomainScore:
        normalized_domain_id = _normalize_required(domain_id, "domain_id").lower()
        for domain_score in self.domain_scores:
            if domain_score.domain_id == normalized_domain_id:
                return domain_score
        raise KeyError(f"Unknown domain score: {normalized_domain_id}")

    def to_manifest(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "taxonomy_version": self.taxonomy_version,
            "scoring_version": self.scoring_version,
            "seed": self.seed,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "strict_coverage": self.strict_coverage,
            "scenario_count": self.scenario_count,
            "overall_score": self.overall_score,
            "deterministic_digest": self.deterministic_digest,
            "domain_scores": [
                {
                    "domain_id": domain.domain_id,
                    "scenario_count": domain.scenario_count,
                    "capability_count": domain.capability_count,
                    "weighted_score": domain.weighted_score,
                    "weight": domain.weight,
                    "capability_scores": [
                        {
                            "capability_id": capability.capability_id,
                            "domain_id": capability.domain_id,
                            "scenario_count": capability.scenario_count,
                            "weighted_score": capability.weighted_score,
                            "weight": capability.weight,
                        }
                        for capability in sorted(
                            domain.capability_scores,
                            key=lambda item: item.capability_id,
                        )
                    ],
                }
                for domain in sorted(self.domain_scores, key=lambda item: item.domain_id)
            ],
            "scenario_results": [
                {
                    "scenario_id": result.scenario_id,
                    "capability_id": result.capability_id,
                    "domain_id": result.domain_id,
                    "difficulty_band_id": result.difficulty_band_id,
                    "scenario_seed": result.scenario_seed,
                    "raw_score": result.raw_score,
                    "normalized_score": result.normalized_score,
                    "weight": result.weight,
                    "difficulty_multiplier": result.difficulty_multiplier,
                    "weighted_score": result.weighted_score,
                    "evidence": dict(result.evidence),
                }
                for result in sorted(self.scenario_results, key=lambda item: item.scenario_id)
            ],
            "metadata": dict(self.metadata),
        }


class BenchmarkScenarioEvaluator(Protocol):
    def evaluate(
        self,
        scenario: BenchmarkScenarioDefinition,
        *,
        random_state: random.Random,
    ) -> BenchmarkScenarioEvaluation:
        """Return an evaluation for a benchmark scenario."""


class BenchmarkHarnessError(ValueError):
    """Raised when benchmark harness execution fails validation."""


class BenchmarkHarnessRunner:
    """Executes benchmark scenarios and computes reproducible weighted scores."""

    def __init__(
        self,
        taxonomy: BenchmarkTaxonomy,
        *,
        scoring_version: str = "deterministic-v1",
        seed: int = 0,
    ) -> None:
        validate_benchmark_taxonomy(taxonomy)

        self.taxonomy = taxonomy
        self.scoring_version = _normalize_required(scoring_version, "scoring_version")
        self.seed = _normalize_seed(seed)

        self._domains_by_id = {domain.domain_id: domain for domain in taxonomy.domains}
        self._capabilities_by_id: dict[str, BenchmarkCapabilityDefinition] = {
            capability.capability_id: capability
            for capability in taxonomy.capabilities
        }
        self._bands_by_id: dict[str, BenchmarkDifficultyBand] = {
            band.band_id: band
            for band in taxonomy.difficulty_bands
        }

    def run_benchmark(
        self,
        scenarios: list[BenchmarkScenarioDefinition] | tuple[BenchmarkScenarioDefinition, ...],
        evaluator: BenchmarkScenarioEvaluator | Callable[..., Any],
        *,
        run_id: str | None = None,
        strict_coverage: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> BenchmarkHarnessRunResult:
        started_at = _utc_now_iso()
        normalized_scenarios = _normalize_scenarios(scenarios)
        if not normalized_scenarios:
            raise BenchmarkHarnessError("scenarios must include at least one scenario")

        evaluator_fn = _resolve_evaluator(evaluator)

        scenario_results: list[BenchmarkScenarioResult] = []
        capability_to_results: dict[str, list[BenchmarkScenarioResult]] = {}

        for scenario in normalized_scenarios:
            capability = self._capabilities_by_id.get(scenario.capability_id)
            if capability is None:
                raise BenchmarkHarnessError(
                    f"Scenario {scenario.scenario_id} references unknown capability {scenario.capability_id}"
                )

            band = self._bands_by_id.get(scenario.difficulty_band_id)
            if band is None:
                raise BenchmarkHarnessError(
                    f"Scenario {scenario.scenario_id} references unknown difficulty band {scenario.difficulty_band_id}"
                )

            scenario_seed = _derive_scenario_seed(
                base_seed=self.seed,
                taxonomy_version=self.taxonomy.taxonomy_version,
                scenario=scenario,
            )
            random_state = random.Random(scenario_seed)

            evaluation = _invoke_evaluator(
                evaluator_fn,
                scenario=scenario,
                random_state=random_state,
            )

            normalized_score = _normalize_score(
                evaluation.raw_score,
                field_name=f"scenario:{scenario.scenario_id}:raw_score",
            )
            weighted_score = normalized_score * scenario.weight * band.multiplier

            result = BenchmarkScenarioResult(
                scenario_id=scenario.scenario_id,
                capability_id=scenario.capability_id,
                domain_id=capability.domain_id,
                difficulty_band_id=scenario.difficulty_band_id,
                scenario_seed=scenario_seed,
                raw_score=normalized_score,
                normalized_score=normalized_score,
                weight=scenario.weight,
                difficulty_multiplier=band.multiplier,
                weighted_score=weighted_score,
                evidence=dict(evaluation.evidence),
            )
            scenario_results.append(result)
            capability_to_results.setdefault(result.capability_id, []).append(result)

        if strict_coverage:
            missing_capabilities = sorted(
                capability_id
                for capability_id in self._capabilities_by_id
                if capability_id not in capability_to_results
            )
            if missing_capabilities:
                raise BenchmarkHarnessError(
                    "Missing scenario coverage for capabilities: "
                    + ", ".join(missing_capabilities)
                )

        capability_scores: list[BenchmarkCapabilityScore] = []
        for capability_id in sorted(capability_to_results):
            results = capability_to_results[capability_id]
            capability = self._capabilities_by_id[capability_id]

            denominator = sum(item.weight * item.difficulty_multiplier for item in results)
            if denominator <= 0:
                raise BenchmarkHarnessError(
                    f"Capability {capability_id} has invalid scenario denominator"
                )

            weighted_score = sum(item.weighted_score for item in results) / denominator
            capability_scores.append(
                BenchmarkCapabilityScore(
                    capability_id=capability_id,
                    domain_id=capability.domain_id,
                    scenario_count=len(results),
                    weighted_score=weighted_score,
                    weight=capability.weight,
                )
            )

        domain_to_capability_scores: dict[str, list[BenchmarkCapabilityScore]] = {}
        for capability_score in capability_scores:
            domain_to_capability_scores.setdefault(
                capability_score.domain_id,
                [],
            ).append(capability_score)

        domain_scores: list[BenchmarkDomainScore] = []
        for domain in sorted(self.taxonomy.domains, key=lambda item: item.domain_id):
            scores = sorted(
                domain_to_capability_scores.get(domain.domain_id, []),
                key=lambda item: item.capability_id,
            )
            if not scores:
                if strict_coverage:
                    raise BenchmarkHarnessError(
                        f"Domain {domain.domain_id} has no scored capabilities"
                    )
                continue

            domain_weight_denominator = sum(item.weight for item in scores)
            if domain_weight_denominator <= 0:
                raise BenchmarkHarnessError(
                    f"Domain {domain.domain_id} has invalid capability weight denominator"
                )

            domain_weighted_score = (
                sum(item.weighted_score * item.weight for item in scores)
                / domain_weight_denominator
            )
            domain_scores.append(
                BenchmarkDomainScore(
                    domain_id=domain.domain_id,
                    scenario_count=sum(item.scenario_count for item in scores),
                    capability_count=len(scores),
                    weighted_score=domain_weighted_score,
                    weight=domain.weight,
                    capability_scores=tuple(scores),
                )
            )

        if not domain_scores:
            raise BenchmarkHarnessError("No scored domains available for benchmark run")

        if strict_coverage:
            overall_score = sum(
                domain.weighted_score * domain.weight
                for domain in domain_scores
            )
        else:
            included_weight = sum(domain.weight for domain in domain_scores)
            if included_weight <= 0:
                raise BenchmarkHarnessError("Domain weight denominator must be positive")
            overall_score = (
                sum(domain.weighted_score * domain.weight for domain in domain_scores)
                / included_weight
            )

        completed_at = _utc_now_iso()
        normalized_run_id = (
            _normalize_required(run_id, "run_id") if run_id is not None else f"benchmark-run-{uuid4()}"
        )

        sorted_scenario_results = tuple(sorted(scenario_results, key=lambda item: item.scenario_id))
        sorted_domain_scores = tuple(sorted(domain_scores, key=lambda item: item.domain_id))
        deterministic_digest = _build_deterministic_digest(
            taxonomy_version=self.taxonomy.taxonomy_version,
            scoring_version=self.scoring_version,
            seed=self.seed,
            strict_coverage=strict_coverage,
            scenario_results=sorted_scenario_results,
            domain_scores=sorted_domain_scores,
            overall_score=overall_score,
        )

        return BenchmarkHarnessRunResult(
            run_id=normalized_run_id,
            taxonomy_version=self.taxonomy.taxonomy_version,
            scoring_version=self.scoring_version,
            seed=self.seed,
            started_at=started_at,
            completed_at=completed_at,
            strict_coverage=bool(strict_coverage),
            scenario_count=len(sorted_scenario_results),
            scenario_results=sorted_scenario_results,
            domain_scores=sorted_domain_scores,
            overall_score=overall_score,
            deterministic_digest=deterministic_digest,
            metadata=dict(metadata or {}),
        )


def _resolve_evaluator(
    evaluator: BenchmarkScenarioEvaluator | Callable[..., Any],
) -> Callable[..., Any]:
    evaluate = getattr(evaluator, "evaluate", None)
    if callable(evaluate):
        return evaluate  # type: ignore[return-value]
    if callable(evaluator):
        return evaluator
    raise TypeError("evaluator must be callable or implement evaluate")


def _invoke_evaluator(
    evaluator_fn: Callable[..., Any],
    *,
    scenario: BenchmarkScenarioDefinition,
    random_state: random.Random,
) -> BenchmarkScenarioEvaluation:
    try:
        raw_result = evaluator_fn(scenario, random_state=random_state)
    except TypeError:
        raw_result = evaluator_fn(scenario)

    if isinstance(raw_result, BenchmarkScenarioEvaluation):
        return raw_result
    if isinstance(raw_result, (int, float)):
        return BenchmarkScenarioEvaluation(raw_score=float(raw_result))

    raise BenchmarkHarnessError(
        f"Evaluator returned unsupported type: {type(raw_result).__name__}"
    )


def _normalize_scenarios(
    scenarios: list[BenchmarkScenarioDefinition] | tuple[BenchmarkScenarioDefinition, ...],
) -> tuple[BenchmarkScenarioDefinition, ...]:
    normalized: list[BenchmarkScenarioDefinition] = []
    seen_ids: set[str] = set()

    for scenario in scenarios:
        if not isinstance(scenario, BenchmarkScenarioDefinition):
            raise TypeError("scenarios must contain BenchmarkScenarioDefinition entries")

        scenario_id = _normalize_required(scenario.scenario_id, "scenario_id").lower()
        if scenario_id in seen_ids:
            raise BenchmarkHarnessError(f"Duplicate scenario_id: {scenario_id}")
        seen_ids.add(scenario_id)

        capability_id = _normalize_required(scenario.capability_id, "capability_id").lower()
        difficulty_band_id = _normalize_required(scenario.difficulty_band_id, "difficulty_band_id").lower()

        if scenario.weight <= 0:
            raise BenchmarkHarnessError(f"Scenario {scenario_id} weight must be positive")

        normalized.append(
            BenchmarkScenarioDefinition(
                scenario_id=scenario_id,
                capability_id=capability_id,
                difficulty_band_id=difficulty_band_id,
                prompt=_normalize_optional(scenario.prompt),
                weight=float(scenario.weight),
                metadata=dict(scenario.metadata),
            )
        )

    return tuple(sorted(normalized, key=lambda item: item.scenario_id))


def _derive_scenario_seed(
    *,
    base_seed: int,
    taxonomy_version: str,
    scenario: BenchmarkScenarioDefinition,
) -> int:
    canonical = "|".join(
        (
            str(base_seed),
            taxonomy_version,
            scenario.scenario_id,
            scenario.capability_id,
            scenario.difficulty_band_id,
        )
    )
    digest = sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def _build_deterministic_digest(
    *,
    taxonomy_version: str,
    scoring_version: str,
    seed: int,
    strict_coverage: bool,
    scenario_results: tuple[BenchmarkScenarioResult, ...],
    domain_scores: tuple[BenchmarkDomainScore, ...],
    overall_score: float,
) -> str:
    canonical_payload = {
        "taxonomy_version": taxonomy_version,
        "scoring_version": scoring_version,
        "seed": seed,
        "strict_coverage": strict_coverage,
        "overall_score": round(overall_score, 12),
        "scenario_results": [
            {
                "scenario_id": result.scenario_id,
                "capability_id": result.capability_id,
                "domain_id": result.domain_id,
                "difficulty_band_id": result.difficulty_band_id,
                "scenario_seed": result.scenario_seed,
                "raw_score": round(result.raw_score, 12),
                "weight": round(result.weight, 12),
                "difficulty_multiplier": round(result.difficulty_multiplier, 12),
                "weighted_score": round(result.weighted_score, 12),
            }
            for result in scenario_results
        ],
        "domain_scores": [
            {
                "domain_id": domain.domain_id,
                "weighted_score": round(domain.weighted_score, 12),
                "weight": round(domain.weight, 12),
                "scenario_count": domain.scenario_count,
                "capability_count": domain.capability_count,
                "capabilities": [
                    {
                        "capability_id": capability.capability_id,
                        "weighted_score": round(capability.weighted_score, 12),
                        "weight": round(capability.weight, 12),
                        "scenario_count": capability.scenario_count,
                    }
                    for capability in domain.capability_scores
                ],
            }
            for domain in domain_scores
        ],
    }
    encoded = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = sha256(encoded).hexdigest()
    return f"bench-{digest[:24]}"


def _normalize_score(value: float, *, field_name: str) -> float:
    try:
        score = float(value)
    except Exception as exc:
        raise BenchmarkHarnessError(f"{field_name} must be numeric") from exc

    if score < 0 or score > 1:
        raise BenchmarkHarnessError(f"{field_name} must be between 0 and 1")
    return score


def _normalize_seed(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("seed must be an integer")
    if value < 0:
        raise BenchmarkHarnessError("seed must be non-negative")
    return value


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise BenchmarkHarnessError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
