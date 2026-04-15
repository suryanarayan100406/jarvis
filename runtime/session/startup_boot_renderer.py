"""Startup boot-sequence rendering and integration state reporting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from .session_protocol import SessionProtocolContract

IntegrationStatus = Literal["online", "degraded", "offline", "unknown"]
BootOverallStatus = Literal["healthy", "degraded", "critical"]

_ALLOWED_INTEGRATION_STATUSES = {"online", "degraded", "offline", "unknown"}


@dataclass(frozen=True)
class IntegrationStateRecord:
    system_id: str
    status: IntegrationStatus
    latency_ms: float | None
    last_checked_at: str
    detail: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class StartupBootReport:
    boot_id: str
    generated_at: str
    address: str
    context_summary: str
    connected_systems: tuple[str, ...]
    overall_status: BootOverallStatus
    online_count: int
    degraded_count: int
    offline_count: int
    unknown_count: int
    deterministic_digest: str
    message: str
    integration_states: tuple[IntegrationStateRecord, ...]
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "boot_id": self.boot_id,
            "generated_at": self.generated_at,
            "address": self.address,
            "context_summary": self.context_summary,
            "connected_systems": list(self.connected_systems),
            "overall_status": self.overall_status,
            "online_count": self.online_count,
            "degraded_count": self.degraded_count,
            "offline_count": self.offline_count,
            "unknown_count": self.unknown_count,
            "deterministic_digest": self.deterministic_digest,
            "message": self.message,
            "integration_states": [
                {
                    "system_id": item.system_id,
                    "status": item.status,
                    "latency_ms": item.latency_ms,
                    "last_checked_at": item.last_checked_at,
                    "detail": item.detail,
                    "metadata": dict(item.metadata),
                }
                for item in sorted(self.integration_states, key=lambda entry: entry.system_id)
            ],
            "metadata": dict(self.metadata),
        }


class StartupBootRenderError(ValueError):
    """Raised when startup boot rendering inputs are invalid."""


class StartupBootRenderer:
    """Builds startup boot messages with integration state summaries."""

    def __init__(self, contract: SessionProtocolContract | None = None) -> None:
        self.contract = contract

    def render_startup(
        self,
        integration_states: list[IntegrationStateRecord] | tuple[IntegrationStateRecord, ...],
        *,
        address: str = "Boss",
        context_summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StartupBootReport:
        normalized_address = _normalize_required(address, "address")
        normalized_context = _normalize_optional(context_summary) or "none"
        normalized_states = _normalize_integration_states(integration_states)

        connected_systems = tuple(
            item.system_id for item in normalized_states if item.status in {"online", "degraded"}
        )

        online_count = sum(1 for item in normalized_states if item.status == "online")
        degraded_count = sum(1 for item in normalized_states if item.status == "degraded")
        offline_count = sum(1 for item in normalized_states if item.status == "offline")
        unknown_count = sum(1 for item in normalized_states if item.status == "unknown")

        if offline_count > 0:
            overall_status: BootOverallStatus = "critical"
        elif degraded_count > 0 or unknown_count > 0:
            overall_status = "degraded"
        else:
            overall_status = "healthy"

        if self.contract is not None:
            boot_message = self.contract.render_boot_message(
                connected_systems=list(connected_systems),
                context_summary=normalized_context,
                address=normalized_address,
            )
        else:
            systems = ", ".join(connected_systems) if connected_systems else "none"
            boot_message = "\n".join(
                [
                    "FRIDAY online. Running system check...",
                    "- Knowledge base: loaded",
                    f"- Connected systems: {systems}",
                    f"- Context from last session: {normalized_context}",
                    "",
                    f"Ready, {normalized_address}. What are we working on?",
                ]
            )

        integration_summary = self._render_integration_summary(
            overall_status=overall_status,
            online_count=online_count,
            degraded_count=degraded_count,
            offline_count=offline_count,
            unknown_count=unknown_count,
            normalized_states=normalized_states,
        )

        full_message = f"{boot_message}\n\n{integration_summary}".strip()

        deterministic_digest = _build_boot_digest(
            address=normalized_address,
            context_summary=normalized_context,
            connected_systems=connected_systems,
            overall_status=overall_status,
            integration_states=normalized_states,
        )

        return StartupBootReport(
            boot_id=f"boot-{deterministic_digest[:20]}",
            generated_at=_utc_now_iso(),
            address=normalized_address,
            context_summary=normalized_context,
            connected_systems=connected_systems,
            overall_status=overall_status,
            online_count=online_count,
            degraded_count=degraded_count,
            offline_count=offline_count,
            unknown_count=unknown_count,
            deterministic_digest=deterministic_digest,
            message=full_message,
            integration_states=normalized_states,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _render_integration_summary(
        *,
        overall_status: BootOverallStatus,
        online_count: int,
        degraded_count: int,
        offline_count: int,
        unknown_count: int,
        normalized_states: tuple[IntegrationStateRecord, ...],
    ) -> str:
        lines = [
            (
                "Integration state: "
                f"{overall_status} "
                f"(online={online_count}, degraded={degraded_count}, "
                f"offline={offline_count}, unknown={unknown_count})"
            )
        ]
        for state in normalized_states:
            latency = f"{state.latency_ms:.1f}ms" if state.latency_ms is not None else "n/a"
            detail = f"; {state.detail}" if state.detail else ""
            lines.append(f"- {state.system_id}: {state.status} (latency={latency}){detail}")
        return "\n".join(lines)


def _normalize_integration_states(
    states: list[IntegrationStateRecord] | tuple[IntegrationStateRecord, ...],
) -> tuple[IntegrationStateRecord, ...]:
    if not isinstance(states, (list, tuple)):
        raise TypeError("integration_states must be list or tuple of IntegrationStateRecord")
    if not states:
        raise StartupBootRenderError("integration_states must include at least one system")

    seen_ids: set[str] = set()
    normalized: list[IntegrationStateRecord] = []

    for state in states:
        if not isinstance(state, IntegrationStateRecord):
            raise TypeError("integration_states must contain IntegrationStateRecord values")

        system_id = _normalize_required(state.system_id, "system_id").lower()
        if system_id in seen_ids:
            raise StartupBootRenderError(f"Duplicate integration system_id: {system_id}")
        seen_ids.add(system_id)

        status = _normalize_required(state.status, f"{system_id}.status").lower()
        if status not in _ALLOWED_INTEGRATION_STATUSES:
            allowed = ", ".join(sorted(_ALLOWED_INTEGRATION_STATUSES))
            raise StartupBootRenderError(
                f"Unsupported integration status {status} for {system_id}. Allowed: {allowed}"
            )

        latency_ms: float | None
        if state.latency_ms is None:
            latency_ms = None
        else:
            if not isinstance(state.latency_ms, (int, float)):
                raise TypeError(f"{system_id}.latency_ms must be numeric or None")
            if state.latency_ms < 0:
                raise StartupBootRenderError(f"{system_id}.latency_ms cannot be negative")
            latency_ms = float(state.latency_ms)

        last_checked_at = _normalize_required(state.last_checked_at, f"{system_id}.last_checked_at")

        normalized.append(
            IntegrationStateRecord(
                system_id=system_id,
                status=status,
                latency_ms=latency_ms,
                last_checked_at=last_checked_at,
                detail=_normalize_optional(state.detail),
                metadata=dict(state.metadata),
            )
        )

    return tuple(sorted(normalized, key=lambda item: item.system_id))


def _build_boot_digest(
    *,
    address: str,
    context_summary: str,
    connected_systems: tuple[str, ...],
    overall_status: BootOverallStatus,
    integration_states: tuple[IntegrationStateRecord, ...],
) -> str:
    canonical = json.dumps(
        {
            "address": address,
            "context_summary": context_summary,
            "connected_systems": list(connected_systems),
            "overall_status": overall_status,
            "integration_states": [
                {
                    "system_id": item.system_id,
                    "status": item.status,
                    "latency_ms": item.latency_ms,
                    "last_checked_at": item.last_checked_at,
                    "detail": item.detail,
                }
                for item in sorted(integration_states, key=lambda entry: entry.system_id)
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
        raise StartupBootRenderError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "IntegrationStatus",
    "BootOverallStatus",
    "IntegrationStateRecord",
    "StartupBootReport",
    "StartupBootRenderError",
    "StartupBootRenderer",
]
