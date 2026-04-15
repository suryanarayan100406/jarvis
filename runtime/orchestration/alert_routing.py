"""Alert rule evaluation and severity-based on-call routing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Any, Literal

from .event_bus import OperationalAlertEvent, OperationalEventBus, SeverityLevel

DispatchStatus = Literal["dispatched", "suppressed", "unrouted"]

_SEVERITY_RANK: dict[str, int] = {
    "info": 0,
    "warning": 1,
    "error": 2,
    "critical": 3,
}


@dataclass(frozen=True)
class AlertRuleDefinition:
    rule_id: str
    event_pattern: str
    min_severity: SeverityLevel
    route_id: str
    suppress_window_seconds: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OnCallRouteDefinition:
    route_id: str
    service_id: str
    primary_contact: str
    secondary_contact: str | None
    escalation_contact: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AlertDispatchRecord:
    dispatch_id: str
    event_id: str
    event_type: str
    severity: SeverityLevel
    status: DispatchStatus
    rule_id: str | None
    route_id: str | None
    target_contact: str | None
    backup_contact: str | None
    reason: str
    dispatched_at: str
    metadata: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "dispatch_id": self.dispatch_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "severity": self.severity,
            "status": self.status,
            "rule_id": self.rule_id,
            "route_id": self.route_id,
            "target_contact": self.target_contact,
            "backup_contact": self.backup_contact,
            "reason": self.reason,
            "dispatched_at": self.dispatched_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AlertRoutingBatchResult:
    subscriber_id: str
    evaluated_count: int
    dispatched_count: int
    suppressed_count: int
    unrouted_count: int
    records: tuple[AlertDispatchRecord, ...]
    processed_at: str


class AlertRoutingError(ValueError):
    """Raised when alert routing configuration or operations are invalid."""


class AlertRoutingEngine:
    """Routes operational alerts to on-call contacts by rule and severity."""

    def __init__(self, event_bus: OperationalEventBus | None = None) -> None:
        self.event_bus = event_bus
        self._rules: dict[str, AlertRuleDefinition] = {}
        self._routes: dict[str, OnCallRouteDefinition] = {}
        self._dispatches: list[AlertDispatchRecord] = []
        self._last_dispatch_by_key: dict[tuple[str, str, str, str], datetime] = {}

    def register_route(self, route: OnCallRouteDefinition) -> OnCallRouteDefinition:
        if not isinstance(route, OnCallRouteDefinition):
            raise TypeError("route must be OnCallRouteDefinition")

        normalized_route = OnCallRouteDefinition(
            route_id=_normalize_required(route.route_id, "route_id").lower(),
            service_id=_normalize_required(route.service_id, "service_id").lower(),
            primary_contact=_normalize_required(route.primary_contact, "primary_contact"),
            secondary_contact=_normalize_optional(route.secondary_contact),
            escalation_contact=_normalize_optional(route.escalation_contact),
            metadata=dict(route.metadata),
        )
        self._routes[normalized_route.route_id] = normalized_route
        return normalized_route

    def register_rule(self, rule: AlertRuleDefinition) -> AlertRuleDefinition:
        if not isinstance(rule, AlertRuleDefinition):
            raise TypeError("rule must be AlertRuleDefinition")

        normalized_rule = AlertRuleDefinition(
            rule_id=_normalize_required(rule.rule_id, "rule_id").lower(),
            event_pattern=_normalize_required(rule.event_pattern, "event_pattern").lower(),
            min_severity=_normalize_severity(rule.min_severity),
            route_id=_normalize_required(rule.route_id, "route_id").lower(),
            suppress_window_seconds=_normalize_window(rule.suppress_window_seconds),
            metadata=dict(rule.metadata),
        )

        if normalized_rule.route_id not in self._routes:
            raise AlertRoutingError(
                f"Rule {normalized_rule.rule_id} references unknown route {normalized_rule.route_id}"
            )

        self._rules[normalized_rule.rule_id] = normalized_rule
        return normalized_rule

    def route_event(self, event: OperationalAlertEvent) -> AlertDispatchRecord:
        if not isinstance(event, OperationalAlertEvent):
            raise TypeError("event must be OperationalAlertEvent")

        matched_rule = self._select_rule(event)
        if matched_rule is None:
            record = self._record(
                event=event,
                status="unrouted",
                rule=None,
                route=None,
                target_contact=None,
                backup_contact=None,
                reason="No matching alert rule",
            )
            return record

        route = self._routes[matched_rule.route_id]
        dispatch_key = (
            matched_rule.rule_id,
            event.source,
            event.event_type,
            event.message,
        )
        occurred_at = _parse_iso(event.occurred_at)

        last_dispatched = self._last_dispatch_by_key.get(dispatch_key)
        if last_dispatched is not None:
            elapsed = (occurred_at - last_dispatched).total_seconds()
            if elapsed < matched_rule.suppress_window_seconds:
                return self._record(
                    event=event,
                    status="suppressed",
                    rule=matched_rule,
                    route=route,
                    target_contact=None,
                    backup_contact=None,
                    reason=(
                        f"Suppressed duplicate within {matched_rule.suppress_window_seconds}s window"
                    ),
                )

        target_contact, backup_contact = _select_contacts(route, event.severity)
        self._last_dispatch_by_key[dispatch_key] = occurred_at

        return self._record(
            event=event,
            status="dispatched",
            rule=matched_rule,
            route=route,
            target_contact=target_contact,
            backup_contact=backup_contact,
            reason=f"Matched rule {matched_rule.rule_id} for severity {event.severity}",
        )

    def process_subscriber_alerts(
        self,
        subscriber_id: str,
        *,
        limit: int = 50,
    ) -> AlertRoutingBatchResult:
        if self.event_bus is None:
            raise AlertRoutingError("event_bus is required for batch processing")

        poll = self.event_bus.poll_subscriber(subscriber_id, limit=limit)
        records: list[AlertDispatchRecord] = []
        for event in poll.events:
            record = self.route_event(event)
            records.append(record)
            self.event_bus.acknowledge(subscriber_id, event.event_id)

        return AlertRoutingBatchResult(
            subscriber_id=poll.subscriber_id,
            evaluated_count=len(records),
            dispatched_count=sum(1 for record in records if record.status == "dispatched"),
            suppressed_count=sum(1 for record in records if record.status == "suppressed"),
            unrouted_count=sum(1 for record in records if record.status == "unrouted"),
            records=tuple(records),
            processed_at=_utc_now_iso(),
        )

    def list_dispatches(self, *, status: DispatchStatus | None = None) -> list[AlertDispatchRecord]:
        dispatches = list(self._dispatches)
        if status is not None:
            normalized_status = _normalize_required(status, "status").lower()
            dispatches = [entry for entry in dispatches if entry.status == normalized_status]
        return sorted(dispatches, key=lambda entry: entry.dispatch_id)

    def _select_rule(self, event: OperationalAlertEvent) -> AlertRuleDefinition | None:
        candidates: list[AlertRuleDefinition] = []
        for rule in self._rules.values():
            if not fnmatch(event.event_type, rule.event_pattern):
                continue
            if _SEVERITY_RANK[event.severity] < _SEVERITY_RANK[rule.min_severity]:
                continue
            candidates.append(rule)

        if not candidates:
            return None

        candidates.sort(
            key=lambda rule: (-_SEVERITY_RANK[rule.min_severity], rule.rule_id)
        )
        return candidates[0]

    def _record(
        self,
        *,
        event: OperationalAlertEvent,
        status: DispatchStatus,
        rule: AlertRuleDefinition | None,
        route: OnCallRouteDefinition | None,
        target_contact: str | None,
        backup_contact: str | None,
        reason: str,
    ) -> AlertDispatchRecord:
        dispatch_id = f"dispatch-{len(self._dispatches) + 1:05d}"
        record = AlertDispatchRecord(
            dispatch_id=dispatch_id,
            event_id=event.event_id,
            event_type=event.event_type,
            severity=event.severity,
            status=status,
            rule_id=(rule.rule_id if rule is not None else None),
            route_id=(route.route_id if route is not None else None),
            target_contact=target_contact,
            backup_contact=backup_contact,
            reason=reason,
            dispatched_at=_utc_now_iso(),
            metadata={
                "source": event.source,
                "message": event.message,
                "payload": dict(event.payload),
                "rule_metadata": (dict(rule.metadata) if rule is not None else {}),
                "route_metadata": (dict(route.metadata) if route is not None else {}),
            },
        )
        self._dispatches.append(record)
        return record


def build_default_alert_routing_engine(
    event_bus: OperationalEventBus | None = None,
) -> AlertRoutingEngine:
    engine = AlertRoutingEngine(event_bus=event_bus)

    engine.register_route(
        OnCallRouteDefinition(
            route_id="runtime-oncall",
            service_id="runtime",
            primary_contact="runtime_primary",
            secondary_contact="runtime_secondary",
            escalation_contact="runtime_manager",
            metadata={"phase": "P11-T3"},
        )
    )
    engine.register_route(
        OnCallRouteDefinition(
            route_id="autonomy-oncall",
            service_id="autonomy",
            primary_contact="autonomy_primary",
            secondary_contact="autonomy_secondary",
            escalation_contact="autonomy_manager",
            metadata={"phase": "P11-T3"},
        )
    )
    engine.register_route(
        OnCallRouteDefinition(
            route_id="security-oncall",
            service_id="security",
            primary_contact="security_primary",
            secondary_contact="security_secondary",
            escalation_contact="security_manager",
            metadata={"phase": "P11-T3"},
        )
    )

    engine.register_rule(
        AlertRuleDefinition(
            rule_id="runtime-alerts",
            event_pattern="ops.runtime.*",
            min_severity="warning",
            route_id="runtime-oncall",
            suppress_window_seconds=300,
            metadata={"phase": "P11-T3"},
        )
    )
    engine.register_rule(
        AlertRuleDefinition(
            rule_id="autonomy-alerts",
            event_pattern="ops.autonomy.*",
            min_severity="warning",
            route_id="autonomy-oncall",
            suppress_window_seconds=300,
            metadata={"phase": "P11-T3"},
        )
    )
    engine.register_rule(
        AlertRuleDefinition(
            rule_id="security-alerts",
            event_pattern="ops.security.*",
            min_severity="info",
            route_id="security-oncall",
            suppress_window_seconds=120,
            metadata={"phase": "P11-T3"},
        )
    )

    return engine


def _select_contacts(
    route: OnCallRouteDefinition,
    severity: SeverityLevel,
) -> tuple[str, str | None]:
    if severity == "critical":
        target = route.escalation_contact or route.secondary_contact or route.primary_contact
        backup = route.secondary_contact if target != route.secondary_contact else route.primary_contact
        return target, backup

    if severity == "error":
        target = route.secondary_contact or route.primary_contact
        backup = route.primary_contact if target != route.primary_contact else None
        return target, backup

    target = route.primary_contact
    backup = route.secondary_contact
    return target, backup


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise AlertRoutingError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _normalize_severity(value: SeverityLevel | str) -> SeverityLevel:
    normalized = _normalize_required(str(value), "severity").lower()
    if normalized not in _SEVERITY_RANK:
        allowed = ", ".join(sorted(_SEVERITY_RANK))
        raise AlertRoutingError(f"Unsupported severity {normalized}. Allowed: {allowed}")
    return normalized  # type: ignore[return-value]


def _normalize_window(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("suppress_window_seconds must be an integer")
    if value < 0:
        raise AlertRoutingError("suppress_window_seconds cannot be negative")
    return value


def _parse_iso(value: str) -> datetime:
    normalized = _normalize_required(value, "occurred_at")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "DispatchStatus",
    "AlertRuleDefinition",
    "OnCallRouteDefinition",
    "AlertDispatchRecord",
    "AlertRoutingBatchResult",
    "AlertRoutingError",
    "AlertRoutingEngine",
    "build_default_alert_routing_engine",
]