"""Escalation workflow for low-confidence autonomous decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from .event_bus import OperationalEventBus

EscalationStatus = Literal["open", "acknowledged", "resolved", "rejected"]


@dataclass(frozen=True)
class EscalationRequest:
    action_id: str
    risk_level: str
    confidence_score: float
    route: str
    reason: str
    context: dict[str, Any]


@dataclass(frozen=True)
class EscalationTicket:
    ticket_id: str
    action_id: str
    risk_level: str
    confidence_score: float
    severity: str
    route: str
    queue: str
    status: EscalationStatus
    reason: str
    context: dict[str, Any]
    created_at: str
    updated_at: str
    resolved_by: str | None
    resolution_note: str | None


class EscalationWorkflowError(ValueError):
    """Raised when escalation workflow operations are invalid."""


class EscalationWorkflowManager:
    """Creates and manages escalation tickets for uncertain or high-risk decisions."""

    def __init__(self, event_bus: OperationalEventBus | None = None) -> None:
        self.event_bus = event_bus
        self._tickets: dict[str, EscalationTicket] = {}

    def create_ticket(
        self,
        request: EscalationRequest,
        *,
        queue: str = "human-ops",
        ticket_id: str | None = None,
    ) -> EscalationTicket:
        action_id = _normalize_required(request.action_id, "action_id")
        risk_level = _normalize_required(request.risk_level, "risk_level").lower()
        route = _normalize_required(request.route, "route").lower()
        reason = _normalize_required(request.reason, "reason")
        confidence_score = _normalize_confidence(request.confidence_score)
        normalized_queue = _normalize_required(queue, "queue")
        assigned_ticket_id = _normalize_required(ticket_id or str(uuid4()), "ticket_id")

        if assigned_ticket_id in self._tickets:
            raise EscalationWorkflowError(f"Ticket already exists: {assigned_ticket_id}")

        now = _utc_now_iso()
        severity = _derive_severity(risk_level=risk_level, confidence_score=confidence_score)

        ticket = EscalationTicket(
            ticket_id=assigned_ticket_id,
            action_id=action_id,
            risk_level=risk_level,
            confidence_score=confidence_score,
            severity=severity,
            route=route,
            queue=normalized_queue,
            status="open",
            reason=reason,
            context=dict(request.context),
            created_at=now,
            updated_at=now,
            resolved_by=None,
            resolution_note=None,
        )
        self._tickets[ticket.ticket_id] = ticket
        self._emit_event("escalation.created", ticket)
        return ticket

    def acknowledge_ticket(self, ticket_id: str, *, actor_id: str) -> EscalationTicket:
        ticket = self.get_ticket(ticket_id)
        if ticket.status != "open":
            raise EscalationWorkflowError("Only open tickets can be acknowledged")

        _normalize_required(actor_id, "actor_id")
        updated = self._replace_ticket(ticket, status="acknowledged")
        self._emit_event("escalation.acknowledged", updated)
        return updated

    def resolve_ticket(
        self,
        ticket_id: str,
        *,
        actor_id: str,
        approved: bool,
        note: str,
    ) -> EscalationTicket:
        ticket = self.get_ticket(ticket_id)
        if ticket.status not in {"open", "acknowledged"}:
            raise EscalationWorkflowError("Only open or acknowledged tickets can be resolved")

        normalized_actor = _normalize_required(actor_id, "actor_id")
        normalized_note = _normalize_required(note, "note")
        resolved_status: EscalationStatus = "resolved" if approved else "rejected"

        updated = self._replace_ticket(
            ticket,
            status=resolved_status,
            resolved_by=normalized_actor,
            resolution_note=normalized_note,
        )
        self._emit_event("escalation.resolved" if approved else "escalation.rejected", updated)
        return updated

    def get_ticket(self, ticket_id: str) -> EscalationTicket:
        normalized_id = _normalize_required(ticket_id, "ticket_id")
        ticket = self._tickets.get(normalized_id)
        if ticket is None:
            raise KeyError(f"Unknown ticket: {normalized_id}")
        return ticket

    def list_tickets(
        self,
        *,
        status: EscalationStatus | None = None,
        queue: str | None = None,
        severity: str | None = None,
    ) -> list[EscalationTicket]:
        normalized_status = _normalize_optional(status)
        normalized_queue = _normalize_optional(queue)
        normalized_severity = _normalize_optional(severity)

        tickets = list(self._tickets.values())
        if normalized_status is not None:
            tickets = [ticket for ticket in tickets if ticket.status == normalized_status]
        if normalized_queue is not None:
            tickets = [ticket for ticket in tickets if ticket.queue == normalized_queue]
        if normalized_severity is not None:
            tickets = [ticket for ticket in tickets if ticket.severity == normalized_severity]

        tickets.sort(key=lambda item: (item.created_at, item.ticket_id))
        return tickets

    def _replace_ticket(
        self,
        ticket: EscalationTicket,
        *,
        status: EscalationStatus,
        resolved_by: str | None = None,
        resolution_note: str | None = None,
    ) -> EscalationTicket:
        updated = EscalationTicket(
            ticket_id=ticket.ticket_id,
            action_id=ticket.action_id,
            risk_level=ticket.risk_level,
            confidence_score=ticket.confidence_score,
            severity=ticket.severity,
            route=ticket.route,
            queue=ticket.queue,
            status=status,
            reason=ticket.reason,
            context=dict(ticket.context),
            created_at=ticket.created_at,
            updated_at=_utc_now_iso(),
            resolved_by=resolved_by,
            resolution_note=resolution_note,
        )
        self._tickets[ticket.ticket_id] = updated
        return updated

    def _emit_event(self, event_type: str, ticket: EscalationTicket) -> None:
        if self.event_bus is None:
            return
        self.event_bus.publish(
            event_type=event_type,
            severity=ticket.severity,
            source="escalation.workflow",
            message=f"Escalation {ticket.status}: {ticket.ticket_id}",
            payload={
                "ticket_id": ticket.ticket_id,
                "action_id": ticket.action_id,
                "status": ticket.status,
                "queue": ticket.queue,
                "risk_level": ticket.risk_level,
                "confidence_score": ticket.confidence_score,
            },
        )


def _derive_severity(*, risk_level: str, confidence_score: float) -> str:
    if risk_level == "critical" or confidence_score < 0.35:
        return "critical"
    if risk_level == "high" or confidence_score < 0.55:
        return "error"
    return "warning"


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise EscalationWorkflowError(f"{field_name} is required")
    return normalized


def _normalize_optional(value: object) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _normalize_confidence(value: float) -> float:
    if value < 0 or value > 1:
        raise EscalationWorkflowError("confidence_score must be between 0 and 1")
    return float(value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "EscalationRequest",
    "EscalationStatus",
    "EscalationTicket",
    "EscalationWorkflowError",
    "EscalationWorkflowManager",
]
