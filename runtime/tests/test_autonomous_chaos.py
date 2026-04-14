"""Chaos tests for P6-T12 trigger storms and partial subsystem failures."""

from __future__ import annotations

import unittest

from runtime.orchestration import (
    AutonomousScheduler,
    OperationalEventBus,
    RunWatchdog,
    RunbookExecutionEngine,
    RunbookStep,
)


class AutonomousChaosTests(unittest.TestCase):
    def test_trigger_storm_does_not_duplicate_same_minute_activations(self) -> None:
        scheduler = AutonomousScheduler()
        for index in range(0, 30):
            scheduler.register_cron_trigger(
                trigger_id=f"cron-{index}",
                name=f"storm-{index}",
                expression="* * * * *",
                payload={"idx": index},
            )

        first = scheduler.poll_due(reference_time="2026-04-21T10:00:10Z")
        second = scheduler.poll_due(reference_time="2026-04-21T10:00:40Z")

        self.assertEqual(first.due_count, 30)
        self.assertEqual(second.due_count, 0)

    def test_event_bus_handles_high_volume_without_cross_subscriber_leakage(self) -> None:
        bus = OperationalEventBus()
        bus.subscribe(subscriber_id="ops", event_patterns=["ops.alert.*"], min_severity="warning")
        bus.subscribe(subscriber_id="sec", event_patterns=["security.alert.*"], min_severity="warning")

        for index in range(0, 200):
            bus.publish(
                event_id=f"ops-{index}",
                event_type="ops.alert.health",
                severity="warning",
                source="scheduler.main",
                message=f"ops event {index}",
            )
            bus.publish(
                event_id=f"sec-{index}",
                event_type="security.alert.access",
                severity="error",
                source="watchdog.auth",
                message=f"security event {index}",
            )

        ops = bus.poll_subscriber("ops", limit=500)
        sec = bus.poll_subscriber("sec", limit=500)

        self.assertEqual(ops.pending_total, 200)
        self.assertEqual(sec.pending_total, 200)
        self.assertTrue(all(event.event_type.startswith("ops.alert") for event in ops.events))
        self.assertTrue(all(event.event_type.startswith("security.alert") for event in sec.events))

    def test_partial_handler_outage_with_fallback_prevents_full_pipeline_failure(self) -> None:
        def flaky(_step: RunbookStep, _context: dict) -> str:
            raise RuntimeError("handler unavailable")

        def fallback(_step: RunbookStep, _context: dict) -> str:
            return "fallback-ok"

        engine = RunbookExecutionEngine(handlers={"flaky": flaky, "fallback": fallback})
        engine.register_runbook(
            runbook_id="rb-chaos",
            name="Chaos fallback",
            steps=[
                RunbookStep(
                    step_id="s1",
                    action="flaky",
                    parameters={},
                    fallback_action="fallback",
                )
            ],
        )

        result = engine.execute_runbook("rb-chaos")

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.step_results[0].status, "fallback_succeeded")

    def test_watchdog_recovers_then_terminalizes_storm_runs_within_budget(self) -> None:
        watchdog = RunWatchdog(stuck_timeout_seconds=30, restart_cooldown_seconds=0)
        for index in range(0, 50):
            watchdog.track_run(
                run_id=f"run-{index}",
                max_restarts=1,
                last_progress_at="2026-04-21T10:00:00Z",
            )

        first = watchdog.scan(reference_time="2026-04-21T10:01:00Z")
        second = watchdog.scan(reference_time="2026-04-21T10:02:00Z")

        self.assertEqual(first.restarted, 50)
        self.assertEqual(second.terminalized, 50)

    def test_end_to_end_chaos_mix_remains_operational(self) -> None:
        scheduler = AutonomousScheduler()
        scheduler.register_cron_trigger(
            trigger_id="storm",
            name="storm",
            expression="* * * * *",
            payload={"runbook": "rb"},
        )

        bus = OperationalEventBus()
        bus.subscribe(subscriber_id="ops", event_patterns=["ops.alert.*"], min_severity="warning")

        def unstable(step: RunbookStep, context: dict) -> str:
            minute = int(str(context.get("minute", "0")))
            if minute % 4 == 0:
                raise RuntimeError("unstable")
            return "ok"

        engine = RunbookExecutionEngine(handlers={"unstable": unstable, "fallback": lambda _step, _ctx: "fb"})
        engine.register_runbook(
            runbook_id="rb",
            name="storm runbook",
            steps=[
                RunbookStep(
                    step_id="s1",
                    action="unstable",
                    parameters={},
                    fallback_action="fallback",
                )
            ],
        )

        successes = 0
        degraded = 0
        for minute in range(0, 30):
            ts = f"2026-04-21T10:{minute:02d}:00Z"
            due = scheduler.poll_due(reference_time=ts)
            for activation in due.activations:
                result = engine.execute_runbook("rb", context={"minute": minute, "trigger": activation.trigger_id})
                if result.status == "success":
                    successes += 1
                if result.status == "degraded":
                    degraded += 1
                bus.publish(
                    event_type="ops.alert.health",
                    severity="warning",
                    source="chaos.mix",
                    message=f"cycle={minute} status={result.status}",
                )

        polled = bus.poll_subscriber("ops", limit=200)
        self.assertGreater(successes + degraded, 0)
        self.assertGreater(degraded, 0)
        self.assertGreater(polled.pending_total, 0)


if __name__ == "__main__":
    unittest.main()
