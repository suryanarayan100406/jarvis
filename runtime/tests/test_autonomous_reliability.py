"""Reliability tests for P6-T11 long-duration autonomous execution."""

from __future__ import annotations

import unittest

from runtime.orchestration import (
    AutonomousScheduler,
    OperationalEventBus,
    RunWatchdog,
    RunbookExecutionEngine,
    RunbookStep,
)


class AutonomousReliabilityTests(unittest.TestCase):
    def test_repeated_scheduler_polls_do_not_duplicate_same_minute_trigger(self) -> None:
        scheduler = AutonomousScheduler()
        scheduler.register_cron_trigger(
            trigger_id="cron.health",
            name="Health",
            expression="*/5 * * * *",
            payload={"runbook": "health"},
        )

        emitted = 0
        for second in range(0, 60):
            result = scheduler.poll_due(reference_time=f"2026-04-20T10:15:{second:02d}Z")
            emitted += result.due_count

        self.assertEqual(emitted, 1)

    def test_event_bus_ack_stability_over_many_cycles(self) -> None:
        bus = OperationalEventBus()
        bus.subscribe(subscriber_id="ops", event_patterns=["ops.alert.*"], min_severity="warning")

        for index in range(0, 120):
            bus.publish(
                event_id=f"evt-{index}",
                event_type="ops.alert.health",
                severity="warning",
                source="scheduler.main",
                message=f"event {index}",
                occurred_at=f"2026-04-20T10:{index // 60:02d}:{index % 60:02d}Z",
            )

        polled = bus.poll_subscriber("ops", limit=200)
        self.assertEqual(polled.pending_total, 120)

        for event in polled.events:
            self.assertTrue(bus.acknowledge("ops", event.event_id))

        after = bus.poll_subscriber("ops", limit=200)
        self.assertEqual(after.pending_total, 0)

    def test_runbook_engine_recovers_with_fallback_over_many_runs(self) -> None:
        attempts = {"count": 0}

        def flaky_primary(_step: RunbookStep, _context: dict) -> str:
            attempts["count"] += 1
            if attempts["count"] % 3 == 0:
                return "ok"
            raise RuntimeError("transient")

        def fallback(_step: RunbookStep, _context: dict) -> str:
            return "fallback"

        engine = RunbookExecutionEngine(handlers={"primary": flaky_primary, "fallback": fallback})
        engine.register_runbook(
            runbook_id="rb-rel",
            name="Reliability runbook",
            steps=[
                RunbookStep(
                    step_id="s1",
                    action="primary",
                    parameters={},
                    max_attempts=1,
                    fallback_action="fallback",
                )
            ],
        )

        statuses: list[str] = []
        for _ in range(0, 30):
            result = engine.execute_runbook("rb-rel")
            statuses.append(result.status)

        self.assertTrue(all(status in {"success", "degraded"} for status in statuses))
        self.assertGreater(statuses.count("degraded"), 0)
        self.assertGreater(statuses.count("success"), 0)

    def test_watchdog_restart_budget_bounds_failure_rate(self) -> None:
        watchdog = RunWatchdog(stuck_timeout_seconds=60, restart_cooldown_seconds=0)

        for index in range(0, 40):
            watchdog.track_run(
                run_id=f"run-{index}",
                max_restarts=1,
                last_progress_at="2026-04-20T10:00:00Z",
            )

        first_scan = watchdog.scan(reference_time="2026-04-20T10:02:00Z")
        second_scan = watchdog.scan(reference_time="2026-04-20T10:04:00Z")

        self.assertEqual(first_scan.restarted, 40)
        self.assertEqual(second_scan.terminalized, 40)

    def test_end_to_end_autonomous_cycles_remain_within_expected_health_budget(self) -> None:
        scheduler = AutonomousScheduler()
        scheduler.register_cron_trigger(
            trigger_id="cron.ops",
            name="Ops cycle",
            expression="*/10 * * * *",
            payload={"workflow": "ops"},
        )

        bus = OperationalEventBus()
        bus.subscribe(subscriber_id="ops", event_patterns=["ops.alert.*"], min_severity="warning")

        engine = RunbookExecutionEngine(
            handlers={
                "run": lambda _step, _context: "ok",
            }
        )
        engine.register_runbook(
            runbook_id="rb-ops",
            name="Ops",
            steps=[RunbookStep(step_id="s1", action="run", parameters={})],
        )

        success = 0
        degraded = 0

        for minute in range(0, 180):
            timestamp = f"2026-04-20T10:{minute % 60:02d}:00Z"
            due = scheduler.poll_due(reference_time=timestamp)
            for activation in due.activations:
                result = engine.execute_runbook("rb-ops", context={"trigger": activation.trigger_id})
                if result.status == "success":
                    success += 1
                elif result.status == "degraded":
                    degraded += 1
                bus.publish(
                    event_type="ops.alert.health",
                    severity="warning",
                    source="scheduler.main",
                    message=f"{result.status} cycle",
                )

        polled = bus.poll_subscriber("ops", limit=1000)
        self.assertGreaterEqual(success + degraded, 3)
        self.assertGreater(polled.pending_total, 0)


if __name__ == "__main__":
    unittest.main()
