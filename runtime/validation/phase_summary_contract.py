"""Schema contract and validator for phase summary artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping

SummaryArtifactStatus = Literal["completed", "partial", "blocked"]


@dataclass(frozen=True)
class PhaseSummaryRecord:
    phase_id: str
    plan_id: str
    title: str
    status: SummaryArtifactStatus
    requirements_completed: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    generated_at: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "phase_id": self.phase_id,
            "plan_id": self.plan_id,
            "title": self.title,
            "status": self.status,
            "requirements_completed": list(self.requirements_completed),
            "evidence_refs": list(self.evidence_refs),
            "generated_at": self.generated_at,
            "metadata": dict(sorted(self.metadata.items())),
        }


class PhaseSummaryContractError(ValueError):
    """Raised when phase summary artifact input is invalid."""


def parse_summary_frontmatter(markdown_text: str) -> dict[str, Any]:
    if not isinstance(markdown_text, str):
        raise TypeError("markdown_text must be a string")

    lines = markdown_text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        raise PhaseSummaryContractError("Summary artifact must start with YAML-style frontmatter")

    closing_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        raise PhaseSummaryContractError("Summary artifact frontmatter is missing closing marker")

    parsed: dict[str, Any] = {}
    for line in lines[1:closing_index]:
        stripped = line.strip()
        if not stripped:
            continue
        if ":" not in stripped:
            raise PhaseSummaryContractError("Frontmatter entries must use key: value format")

        key_raw, value_raw = stripped.split(":", 1)
        key = _normalize_required(key_raw, "frontmatter_key").lower()
        if key in parsed:
            raise PhaseSummaryContractError(f"Duplicate frontmatter key: {key}")

        parsed[key] = _parse_frontmatter_value(value_raw.strip())

    return parsed


def build_phase_summary_record(frontmatter: Mapping[str, Any]) -> PhaseSummaryRecord:
    if not isinstance(frontmatter, Mapping):
        raise TypeError("frontmatter must be a mapping")

    phase_id = _normalize_required(
        _pick_field(frontmatter, "phase_id", "phase"),
        "phase_id",
    )
    plan_id = _normalize_required(
        _pick_field(frontmatter, "plan_id", "plan"),
        "plan_id",
    )
    title = _normalize_required(
        _pick_field(frontmatter, "title"),
        "title",
    )
    status = _normalize_status(
        _pick_field(frontmatter, "status"),
    )

    requirements_completed = _normalize_list(
        _pick_field(frontmatter, "requirements_completed", "requirements"),
        field_name="requirements_completed",
        item_prefix="FR-",
    )
    evidence_refs = _normalize_list(
        _pick_field(frontmatter, "evidence_refs", "evidence"),
        field_name="evidence_refs",
    )
    generated_at = _normalize_timestamp(
        _pick_field(frontmatter, "generated_at", "timestamp"),
        field_name="generated_at",
    )

    metadata_raw = frontmatter.get("metadata", {})
    if not isinstance(metadata_raw, Mapping):
        raise PhaseSummaryContractError("metadata must be a mapping when provided")
    metadata = {str(key): value for key, value in metadata_raw.items()}

    return PhaseSummaryRecord(
        phase_id=phase_id,
        plan_id=plan_id,
        title=title,
        status=status,
        requirements_completed=requirements_completed,
        evidence_refs=evidence_refs,
        generated_at=generated_at,
        metadata=metadata,
    )


def validate_phase_summary_artifact(markdown_text: str) -> PhaseSummaryRecord:
    frontmatter = parse_summary_frontmatter(markdown_text)
    return build_phase_summary_record(frontmatter)


def required_summary_fields() -> tuple[str, ...]:
    return (
        "phase_id",
        "plan_id",
        "title",
        "status",
        "requirements_completed",
        "evidence_refs",
        "generated_at",
    )


def _pick_field(frontmatter: Mapping[str, Any], primary: str, *aliases: str) -> Any:
    lowered = {str(key).lower(): value for key, value in frontmatter.items()}
    if primary.lower() in lowered:
        return lowered[primary.lower()]
    for alias in aliases:
        if alias.lower() in lowered:
            return lowered[alias.lower()]
    raise PhaseSummaryContractError(
        f"Missing required frontmatter field: {primary}"
    )


def _normalize_required(value: Any, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise PhaseSummaryContractError(f"{field_name} is required")
    return normalized


def _normalize_status(value: Any) -> SummaryArtifactStatus:
    normalized = _normalize_required(value, "status").lower()
    allowed = {"completed", "partial", "blocked"}
    if normalized not in allowed:
        raise PhaseSummaryContractError(
            "Unsupported status " + normalized + ". Allowed: completed, partial, blocked"
        )
    return normalized  # type: ignore[return-value]


def _normalize_list(value: Any, *, field_name: str, item_prefix: str | None = None) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_items = _split_list_like_string(value)
    elif isinstance(value, (list, tuple)):
        raw_items = [str(item) for item in value]
    else:
        raise PhaseSummaryContractError(f"{field_name} must be a list or string")

    normalized_items: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        normalized = _normalize_required(raw, field_name)
        if item_prefix and not normalized.upper().startswith(item_prefix):
            raise PhaseSummaryContractError(
                f"{field_name} values must start with {item_prefix}; got {normalized}"
            )
        canonical = normalized.upper() if item_prefix else normalized
        if canonical in seen:
            continue
        seen.add(canonical)
        normalized_items.append(canonical)

    if not normalized_items:
        raise PhaseSummaryContractError(f"{field_name} must include at least one value")
    return tuple(normalized_items)


def _normalize_timestamp(value: Any, *, field_name: str) -> str:
    normalized = _normalize_required(value, field_name)
    probe = normalized[:-1] + "+00:00" if normalized.endswith("Z") else normalized
    try:
        datetime.fromisoformat(probe)
    except ValueError as exc:
        raise PhaseSummaryContractError(
            f"{field_name} must be ISO-8601 datetime"
        ) from exc
    return normalized


def _split_list_like_string(value: str) -> list[str]:
    stripped = value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1]
    if not stripped:
        return []

    items = []
    for item in stripped.split(","):
        token = item.strip().strip('"').strip("'")
        if token:
            items.append(token)
    return items


def _parse_frontmatter_value(raw_value: str) -> Any:
    value = raw_value.strip()
    if value.startswith("[") and value.endswith("]"):
        return _split_list_like_string(value)
    return value


__all__ = [
    "SummaryArtifactStatus",
    "PhaseSummaryRecord",
    "PhaseSummaryContractError",
    "parse_summary_frontmatter",
    "build_phase_summary_record",
    "validate_phase_summary_artifact",
    "required_summary_fields",
]
