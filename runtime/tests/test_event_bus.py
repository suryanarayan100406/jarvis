"""Tests for P6-T2 operational event bus subscriptions."""

from __future__ import annotations

import unittest

from runtime.orchestration import EventBusError, OperationalEventBus


class OperationalEventBusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = OperationalEventBus()

    def test_subscribe_and_list(self) -> None:
        self.bus.subscribe(
            subscriber_id="ops",
            event_patterns=["ops.alert.*"],
            min_severity="warning",
            source_prefix="scheduler",
        )

        subscriptions = self.bus.list_subscriptions()

        self.assertEqual(len(subscriptions), 1)
        self.assertEqual(subscriptions[0].subscriber_id, "ops")
        self.assertEqual(subscriptions[0].event_patterns, ("ops.alert.*",))

    def test_publish_and_poll_with_filters(self) -> None:
        self.bus.subscribe(
            subscriber_id="ops",
            event_patterns=["ops.alert.*"],
            min_severity="error",
            source_prefix="scheduler",
        )

        self.bus.publish(
            event_id="evt-info",
            event_type="ops.alert.health",
            severity="info",
            source="scheduler.main",
            message="healthy",
            occurred_at="2026-04-15T10:00:00Z",
        )
        self.bus.publish(
            event_id="evt-error",
            event_type="ops.alert.health",
            severity="error",
            source="scheduler.main",
            message="unhealthy",
            occurred_at="2026-04-15T10:01:00Z",
        )
        self.bus.publish(
            event_id="evt-other-source",
            event_type="ops.alert.health",
            severity="critical",
            source="runbook.worker",
            message="outage",
            occurred_at="2026-04-15T10:02:00Z",
        )

        result = self.bus.poll_subscriber("ops")

        self.assertEqual(result.returned, 1)
        self.assertEqual(result.pending_total, 1)
        self.assertEqual(result.events[0].event_id, "evt-error")

    def test_acknowledgment_removes_event_from_pending(self) -> None:
        self.bus.subscribe(subscriber_id="ops", event_patterns=["ops.alert.*"], min_severity="warning")
        self.bus.publish(
            event_id="evt-warning",
            event_type="ops.alert.health",
            severity="warning",
            source="scheduler.main",
            message="degraded",
            occurred_at="2026-04-15T10:00:00Z",
        )

        before = self.bus.poll_subscriber("ops")
        acknowledged = self.bus.acknowledge("ops", "evt-warning")
        after = self.bus.poll_subscriber("ops")
        include_ack = self.bus.poll_subscriber("ops", include_acknowledged=True)

        self.assertEqual(before.pending_total, 1)
        self.assertTrue(acknowledged)
        self.assertEqual(after.pending_total, 0)
        self.assertEqual(include_ack.returned, 1)

    def test_multiple_subscribers_receive_independent_views(self) -> None:
        self.bus.subscribe(subscriber_id="ops", event_patterns=["ops.alert.*"], min_severity="warning")
        self.bus.subscribe(subscriber_id="security", event_patterns=["security.alert.*"], min_severity="warning")

        self.bus.publish(
            event_id="evt-ops",
            event_type="ops.alert.health",
            severity="critical",
            source="scheduler.main",
            message="ops outage",
            occurred_at="2026-04-15T10:00:00Z",
        )
        self.bus.publish(
            event_id="evt-security",
            event_type="security.alert.access",
            severity="error",
            source="watchdog.auth",
            message="access anomaly",
            occurred_at="2026-04-15T10:00:30Z",
        )

        ops = self.bus.poll_subscriber("ops")
        security = self.bus.poll_subscriber("security")

        self.assertEqual(ops.returned, 1)
        self.assertEqual(ops.events[0].event_id, "evt-ops")
        self.assertEqual(security.returned, 1)
        self.assertEqual(security.events[0].event_id, "evt-security")

    def test_unsubscribe_removes_subscription(self) -> None:
        self.bus.subscribe(subscriber_id="ops")

        removed = self.bus.unsubscribe("ops")
        removed_missing = self.bus.unsubscribe("ops")

        self.assertTrue(removed)
        self.assertFalse(removed_missing)

    def test_invalid_inputs_raise(self) -> None:
        with self.assertRaises(EventBusError):
            self.bus.subscribe(subscriber_id="ops", min_severity="urgent")

        self.bus.subscribe(subscriber_id="ops")
        with self.assertRaises(EventBusError):
            self.bus.poll_subscriber("ops", limit=0)


if __name__ == "__main__":
    unittest.main()
