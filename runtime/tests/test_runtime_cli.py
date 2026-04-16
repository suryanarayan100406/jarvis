"""Tests for P2-T10 runtime CLI commands."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.cli import run_cli
from runtime.store import LocalRunStore


class RuntimeCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runs.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_submit_creates_completed_run(self) -> None:
        code, stdout, stderr = self._run(
            "submit",
            "--goal",
            "Collect diagnostics",
            "--actor-id",
            "boss",
            "--run-id",
            "run-cli-1",
        )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")

        payload = json.loads(stdout)
        self.assertEqual(payload["run_id"], "run-cli-1")
        self.assertEqual(payload["status"], "completed")

        code, status_stdout, _ = self._run("status", "--run-id", "run-cli-1")
        self.assertEqual(code, 0)
        status_payload = json.loads(status_stdout)
        self.assertEqual(status_payload["run"]["status"], "completed")
        self.assertGreaterEqual(len(status_payload["recent_events"]), 1)

    def test_submit_rejects_duplicate_run_id(self) -> None:
        first_code, _, _ = self._run(
            "submit",
            "--goal",
            "Collect diagnostics",
            "--actor-id",
            "boss",
            "--run-id",
            "run-duplicate",
        )
        second_code, second_stdout, second_stderr = self._run(
            "submit",
            "--goal",
            "Collect diagnostics",
            "--actor-id",
            "boss",
            "--run-id",
            "run-duplicate",
        )

        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 1)
        self.assertEqual(second_stdout, "")
        error_payload = json.loads(second_stderr)
        self.assertEqual(error_payload["error_code"], "run_exists")

    def test_stop_cancels_running_run(self) -> None:
        store = LocalRunStore(self.db_path)
        store.apply_migrations()
        store.create_run("run-stop-1", "Inspect health", "boss", status="running")

        code, stdout, stderr = self._run(
            "stop",
            "--run-id",
            "run-stop-1",
            "--actor-id",
            "boss",
            "--reason",
            "manual_interrupt",
        )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["status"], "cancelled")
        self.assertTrue(payload["changed"])

        code, status_stdout, _ = self._run("status", "--run-id", "run-stop-1")
        self.assertEqual(code, 0)
        status_payload = json.loads(status_stdout)
        self.assertEqual(status_payload["run"]["status"], "cancelled")

    def test_replay_returns_filtered_redacted_events(self) -> None:
        store = LocalRunStore(self.db_path)
        store.apply_migrations()
        store.create_run("run-replay-1", "Inspect health", "boss", status="completed")
        store.append_event("run-replay-1", "runtime.plan.completed", {"task_count": 2}, severity="info")
        store.append_event("run-replay-1", "runtime.execute.completed", {"status": "success"}, severity="warning")

        code, stdout, stderr = self._run(
            "replay",
            "--run-id",
            "run-replay-1",
            "--event-type",
            "runtime.execute.completed",
            "--severity",
            "warning",
            "--no-payload",
        )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["event_type"], "runtime.execute.completed")
        self.assertEqual(payload["events"][0]["payload"], {"redacted": True})

    def test_status_missing_run_returns_error(self) -> None:
        code, stdout, stderr = self._run("status", "--run-id", "missing-run")

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        payload = json.loads(stderr)
        self.assertEqual(payload["error_code"], "run_not_found")

    def test_assistant_prompt_executes_single_turn(self) -> None:
        code, stdout, stderr = self._run(
            "assistant",
            "--mode",
            "text",
            "--actor-id",
            "boss",
            "--prompt",
            "Summarize open priorities",
        )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertTrue(payload["validation_passed"])
        self.assertIn("summary", payload)
        self.assertIn("run=", payload["summary"])

    def test_assistant_interactive_text_turn_and_exit(self) -> None:
        with patch("builtins.input", side_effect=["collect diagnostics", "/exit"]):
            code, stdout, stderr = self._run(
                "assistant",
                "--mode",
                "text",
                "--actor-id",
                "boss",
                "--language",
                "en",
                "--no-startup-brief",
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("FRIDAY assistant mode online", stdout)
        self.assertIn("FRIDAY> Done, boss. I completed: collect diagnostics.", stdout)
        self.assertNotIn("[run_id:", stdout)
        self.assertIn("Session closed.", stdout)

    def test_assistant_interactive_can_show_last_run_metadata(self) -> None:
        with patch("builtins.input", side_effect=["check status", "/last", "/exit"]):
            code, stdout, stderr = self._run(
                "assistant",
                "--mode",
                "text",
                "--actor-id",
                "boss",
                "--language",
                "en",
                "--no-startup-brief",
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("FRIDAY> last run_id=", stdout)

    def test_assistant_interactive_show_metadata_flag_prints_run_line(self) -> None:
        with patch("builtins.input", side_effect=["collect diagnostics", "/exit"]):
            code, stdout, stderr = self._run(
                "assistant",
                "--mode",
                "text",
                "--actor-id",
                "boss",
                "--language",
                "en",
                "--no-startup-brief",
                "--show-metadata",
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("[run_id:", stdout)

    def test_assistant_defaults_to_hindi_reply_style(self) -> None:
        with patch("builtins.input", side_effect=["collect diagnostics", "/exit"]):
            code, stdout, stderr = self._run(
                "assistant",
                "--mode",
                "text",
                "--actor-id",
                "boss",
                "--no-startup-brief",
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("FRIDAY> Ho gaya, boss. Maine complete kar diya: collect diagnostics.", stdout)

    def test_assistant_no_startup_brief_disables_weather_news_line(self) -> None:
        with patch("builtins.input", side_effect=["/exit"]):
            code, stdout, stderr = self._run(
                "assistant",
                "--mode",
                "text",
                "--actor-id",
                "boss",
                "--language",
                "en",
                "--no-startup-brief",
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("FRIDAY> Good", stdout)
        self.assertNotIn("weather/news", stdout)

    def _run(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = run_cli(args, db_path=self.db_path, stdout=stdout, stderr=stderr)
        return code, stdout.getvalue().strip(), stderr.getvalue().strip()


if __name__ == "__main__":
    unittest.main()
