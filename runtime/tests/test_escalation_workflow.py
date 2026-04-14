"""Tests for P6-T6 escalation workflow manager."""

from __future__ import annotations

import unittest

from runtime.orchestration import (
    EscalationRequest,
    EscalationWorkflowError,
    EscalationWorkflowManager,
    OperationalEventBus,
)


class EscalationWorkflowManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.event_bus = OperationalEventBus()
        self.event_bus.subscribe(subscriber_id="ops", event_patterns=["escalation.*"], min_severity="warning")
        self.workflow = EscalationWorkflowManager(event_bus=self.event_bus)

    def test_create_ticket_derives_severity_and_stores_ticket(self) -> None:
        ticket = self.workflow.create_ticket(
            EscalationRequest(
                action_id="act-1",
                risk_level="high",
                confidence_score=0.42,
                route="escalate_human",
                reason="confidence below threshold",
                context={"runbook": "db-restart"},
            ),
            queue="incident-response",
            ticket_id="esc-1",
        )

        self.assertEqual(ticket.ticket_id, "esc-1")
        self.assertEqual(ticket.status, "open")
        self.assertEqual(ticket.severity, "error")
        self.assertEqual(ticket.queue, "incident-response")

    def test_acknowledge_and_resolve_ticket_lifecycle(self) -> None:
        ticket = self.workflow.create_ticket(
            EscalationRequest(
                action_id="act-2",
                risk_level="critical",
                confidence_score=0.30,
                route="escalate_human",
                reason="critical operation",
                context={},
            ),
            ticket_id="esc-2",
        )

        acknowledged = self.workflow.acknowledge_ticket(ticket.ticket_id, actor_id="operator-1")
        resolved = self.workflow.resolve_ticket(
            ticket.ticket_id,
            actor_id="operator-1",
            approved=True,
            note="approved after review",
        )

        self.assertEqual(acknowledged.status, "acknowledged")
        self.assertEqual(resolved.status, "resolved")
        self.assertEqual(resolved.resolved_by, "operator-1")
        self.assertEqual(resolved.resolution_note, "approved after review")

    def test_rejected_ticket_is_recorded(self) -> None:
        ticket = self.workflow.create_ticket(
            EscalationRequest(
                action_id="act-3",
                risk_level="medium",
                confidence_score=0.40,
                route="escalate_human",
                reason="uncertain state",
                context={},
            ),
            ticket_id="esc-3",
        )

        rejected = self.workflow.resolve_ticket(
            ticket.ticket_id,
            actor_id="reviewer",
            approved=False,
            note="insufficient evidence",
        )

        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(rejected.resolution_note, "insufficient evidence")

    def test_event_bus_receives_escalation_events(self) -> None:
        ticket = self.workflow.create_ticket(
            EscalationRequest(
                action_id="act-4",
                risk_level="high",
                confidence_score=0.49,
                route="escalate_human",
                reason="risk escalation",
                context={},
            ),
            ticket_id="esc-4",
        )
        self.workflow.acknowledge_ticket(ticket.ticket_id, actor_id="ops-1")

        polled = self.event_bus.poll_subscriber("ops", include_acknowledged=True)

        self.assertGreaterEqual(polled.returned, 2)
        event_types = {event.event_type for event in polled.events}
        self.assertIn("escalation.created", event_types)
        self.assertIn("escalation.acknowledged", event_types)

    def test_invalid_transition_raises(self) -> None:
        ticket = self.workflow.create_ticket(
            EscalationRequest(
                action_id="act-5",
                risk_level="medium",
                confidence_score=0.5,
                route="requires_supervisor",
                reason="needs review",
                context={},
            ),
            ticket_id="esc-5",
        )
        self.workflow.resolve_ticket(ticket.ticket_id, actor_id="ops", approved=True, note="done")

        with self.assertRaises(EscalationWorkflowError):
            self.workflow.acknowledge_ticket(ticket.ticket_id, actor_id="ops")

    def test_invalid_confidence_raises(self) -> None:
        with self.assertRaises(EscalationWorkflowError):
            self.workflow.create_ticket(
                EscalationRequest(
                    action_id="act-6",
                    risk_level="low",
                    confidence_score=1.4,
                    route="auto_approve",
                    reason="invalid",
                    context={},
                )
            )


if __name__ == "__main__":
    unittest.main()
