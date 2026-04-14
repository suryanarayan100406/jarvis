"""Tests for P6-T8 run watchdog and auto-restart logic."""

from __future__ import annotations

import unittest

from runtime.orchestration import RunWatchdog, WatchdogError


class RunWatchdogTests(unittest.TestCase):
    def test_stuck_run_triggers_restart(self) -> None:
        watchdog = RunWatchdog(stuck_timeout_seconds=60, restart_cooldown_seconds=0)
        watchdog.track_run(
            run_id="run-1",
            max_restarts=2,
            last_progress_at="2026-04-16T10:00:00Z",
        )

        result = watchdog.scan(reference_time="2026-04-16T10:02:00Z")
        run = watchdog.get_run("run-1")

        self.assertEqual(result.stuck, 1)
        self.assertEqual(result.restarted, 1)
        self.assertEqual(run.status, "restarting")
        self.assertEqual(run.restart_attempts, 1)
        self.assertEqual(result.actions[0].action, "restart")

    def test_restart_budget_exhausted_marks_failed(self) -> None:
        watchdog = RunWatchdog(stuck_timeout_seconds=60, restart_cooldown_seconds=0)
        watchdog.track_run(
            run_id="run-2",
            max_restarts=0,
            last_progress_at="2026-04-16T10:00:00Z",
        )

        result = watchdog.scan(reference_time="2026-04-16T10:02:00Z")
        run = watchdog.get_run("run-2")

        self.assertEqual(result.terminalized, 1)
        self.assertEqual(run.status, "failed")
        self.assertEqual(result.actions[0].action, "mark_failed")

    def test_heartbeat_prevents_stuck_detection(self) -> None:
        watchdog = RunWatchdog(stuck_timeout_seconds=60, restart_cooldown_seconds=0)
        watchdog.track_run(run_id="run-3", max_restarts=1, last_progress_at="2026-04-16T10:00:00Z")
        watchdog.heartbeat("run-3", at="2026-04-16T10:01:30Z")

        result = watchdog.scan(reference_time="2026-04-16T10:02:00Z")

        self.assertEqual(result.stuck, 0)
        self.assertEqual(result.actions, ())

    def test_terminal_runs_are_ignored_by_scan(self) -> None:
        watchdog = RunWatchdog(stuck_timeout_seconds=60, restart_cooldown_seconds=0)
        watchdog.track_run(run_id="run-4", max_restarts=2, last_progress_at="2026-04-16T10:00:00Z")
        watchdog.mark_terminal("run-4", status="completed")

        result = watchdog.scan(reference_time="2026-04-16T10:03:00Z")

        self.assertEqual(result.scanned, 0)
        self.assertEqual(result.actions, ())

    def test_restart_cooldown_blocks_immediate_repeated_restart(self) -> None:
        watchdog = RunWatchdog(stuck_timeout_seconds=60, restart_cooldown_seconds=300)
        watchdog.track_run(run_id="run-5", max_restarts=2, last_progress_at="2026-04-16T10:00:00Z")
        first = watchdog.scan(reference_time="2026-04-16T10:02:00Z")
        second = watchdog.scan(reference_time="2026-04-16T10:03:30Z")

        self.assertEqual(first.restarted, 1)
        self.assertEqual(second.terminalized, 1)
        self.assertEqual(second.actions[0].reason, "restart_cooldown_active")

    def test_invalid_configuration_raises(self) -> None:
        with self.assertRaises(WatchdogError):
            RunWatchdog(stuck_timeout_seconds=0)

        with self.assertRaises(WatchdogError):
            RunWatchdog(restart_cooldown_seconds=-1)


if __name__ == "__main__":
    unittest.main()
