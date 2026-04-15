"""Tests for P11-T3 alert rules and on-call routing by severity."""

from __future__ import annotations

import unittest

from runtime.orchestration import (
    AlertRoutingEngine,
    AlertRoutingError,
    AlertRuleDefinition,
    OperationalEventBus,
    OnCallRouteDefinition,
    build_default_alert_routing_engine,
)


class AlertRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = OperationalEventBus()
        self.bus.subscribe(
            subscriber_id="ops",
            event_patterns=["ops.*"],
            min_severity="warning",
        )
        self.engine = build_default_alert_routing_engine(event_bus=self.bus)

    def test_runtime_warning_routes_to_primary_contact(self) -> None:
        event = self.bus.publish(
            event_type="ops.runtime.latency",
            severity="warning",
            source="runtime.monitor",
            message="p95 latency above threshold",
            event_id="evt-runtime-warning",
            occurred_at="2026-05-01T10:00:00Z",
        )

        record = self.engine.route_event(event)

        self.assertEqual(record.status, "dispatched")
        self.assertEqual(record.route_id, "runtime-oncall")
        self.assertEqual(record.target_contact, "runtime_primary")
        self.assertEqual(record.backup_contact, "runtime_secondary")

    def test_security_critical_routes_to_escalation_contact(self) -> None:
        event = self.bus.publish(
            event_type="ops.security.guardrail",
            severity="critical",
            source="security.monitor",
            message="guardrail enforcement dropped",
            event_id="evt-security-critical",
            occurred_at="2026-05-01T10:00:00Z",
        )

        record = self.engine.route_event(event)

        self.assertEqual(record.status, "dispatched")
        self.assertEqual(record.route_id, "security-oncall")
        self.assertEqual(record.target_contact, "security_manager")

    def test_duplicate_alert_within_suppression_window_is_suppressed(self) -> None:
        first = self.bus.publish(
            event_type="ops.runtime.latency",
            severity="warning",
            source="runtime.monitor",
            message="p95 latency above threshold",
            event_id="evt-runtime-1",
            occurred_at="2026-05-01T10:00:00Z",
        )
        second = self.bus.publish(
            event_type="ops.runtime.latency",
            severity="warning",
            source="runtime.monitor",
            message="p95 latency above threshold",
            event_id="evt-runtime-2",
            occurred_at="2026-05-01T10:02:00Z",
        )

        first_record = self.engine.route_event(first)
        second_record = self.engine.route_event(second)

        self.assertEqual(first_record.status, "dispatched")
        self.assertEqual(second_record.status, "suppressed")

    def test_process_subscriber_alerts_routes_and_acknowledges(self) -> None:
        self.bus.publish(
            event_type="ops.runtime.availability",
            severity="warning",
            source="runtime.monitor",
            message="runtime availability drift",
            event_id="evt-batch-1",
        )
        self.bus.publish(
            event_type="ops.autonomy.success",
            severity="error",
            source="autonomy.monitor",
            message="autonomy success ratio degraded",
            event_id="evt-batch-2",
        )
        self.bus.publish(
            event_type="ops.security.audit",
            severity="critical",
            source="security.monitor",
            message="critical audit failure",
            event_id="evt-batch-3",
        )

        batch = self.engine.process_subscriber_alerts("ops", limit=10)

        self.assertEqual(batch.evaluated_count, 3)
        self.assertEqual(batch.dispatched_count, 3)

        after = self.bus.poll_subscriber("ops", limit=10)
        self.assertEqual(after.pending_total, 0)

    def test_event_without_matching_rule_is_unrouted(self) -> None:
        event = self.bus.publish(
            event_type="ops.unknown.pipeline",
            severity="error",
            source="unknown.monitor",
            message="no rule should match this",
            event_id="evt-unmatched",
        )

        record = self.engine.route_event(event)

        self.assertEqual(record.status, "unrouted")
        self.assertIsNone(record.route_id)
        self.assertIsNone(record.target_contact)

    def test_register_rule_rejects_unknown_route(self) -> None:
        engine = AlertRoutingEngine()
        engine.register_route(
            OnCallRouteDefinition(
                route_id="ops-route",
                service_id="ops",
                primary_contact="ops_primary",
                secondary_contact=None,
                escalation_contact=None,
                metadata={},
            )
        )

        with self.assertRaises(AlertRoutingError):
            engine.register_rule(
                AlertRuleDefinition(
                    rule_id="bad-route",
                    event_pattern="ops.test.*",
                    min_severity="warning",
                    route_id="missing-route",
                    suppress_window_seconds=60,
                    metadata={},
                )
            )

    def test_dispatch_manifest_is_deterministic(self) -> None:
        event = self.bus.publish(
            event_type="ops.runtime.latency",
            severity="warning",
            source="runtime.monitor",
            message="latency warning",
            event_id="evt-manifest",
            occurred_at="2026-05-01T10:00:00Z",
        )
        record = self.engine.route_event(event)

        first = record.to_manifest()
        second = record.to_manifest()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()