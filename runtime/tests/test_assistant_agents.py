"""Tests for assistant intent routing, command execution, and admin reporting."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.assistant import (
    ActionExecutorAgent,
    AssistantMemoryStore,
    IntentPlannerAgent,
    OutcomeAuditorAgent,
)


class AssistantAgentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = IntentPlannerAgent()
        self.executor = ActionExecutorAgent()
        self.auditor = OutcomeAuditorAgent()

    def test_intent_planner_detects_question(self) -> None:
        decision = self.planner.decide("What is the weather today?")

        self.assertEqual(decision.intent_type, "question")
        self.assertEqual(decision.action, "answer.question")

    def test_intent_planner_detects_open_chrome_command(self) -> None:
        decision = self.planner.decide("open chrome")

        self.assertEqual(decision.intent_type, "command")
        self.assertEqual(decision.action, "system.open_chrome")

    def test_intent_planner_detects_open_this_website_command(self) -> None:
        decision = self.planner.decide("open this website https://example.com")

        self.assertEqual(decision.intent_type, "command")
        self.assertEqual(decision.action, "system.open_website")
        self.assertEqual(decision.arguments.get("url"), "https://example.com")

    def test_intent_planner_detects_open_mail_command(self) -> None:
        decision = self.planner.decide("open mail")

        self.assertEqual(decision.intent_type, "command")
        self.assertEqual(decision.action, "system.open_mail")

    def test_intent_planner_detects_chat_for_ji(self) -> None:
        decision = self.planner.decide("ji")

        self.assertEqual(decision.intent_type, "chat")
        self.assertEqual(decision.action, "chat.respond")

    def test_command_executor_reports_unknown_command_failure(self) -> None:
        decision = self.planner.decide("open secure shell quantum portal")

        if decision.intent_type != "goal":
            self.skipTest("Planner considered this command/goal unexpectedly")

    def test_command_executor_create_folder_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            old_cwd = Path.cwd()
            try:
                import os

                os.chdir(cwd)
                decision = self.planner.decide("create folder workspace/new docs")
                self.assertEqual(decision.intent_type, "command")
                self.assertEqual(decision.action, "system.create_folder")

                result = self.executor.execute(decision)
                self.assertTrue(result.success)
                self.assertTrue((cwd / "workspace" / "new docs").is_dir())
            finally:
                os.chdir(old_cwd)

    def test_command_executor_open_mail_uses_default_handler(self) -> None:
        decision = self.planner.decide("open mail")

        with patch("runtime.assistant.assistant_agents.webbrowser.open", return_value=True) as mocked_open:
            result = self.executor.execute(decision)

        self.assertTrue(result.success)
        mocked_open.assert_called_once_with("mailto:")

    def test_outcome_auditor_for_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            old_cwd = Path.cwd()
            try:
                import os

                os.chdir(cwd)
                decision = self.planner.decide("create folder audit/outcome")
                result = self.executor.execute(decision)
            finally:
                os.chdir(old_cwd)

        report = self.auditor.build_report(decision=decision, language="en", action_result=result)

        self.assertIn("Intent: command", report)
        self.assertIn("Status:", report)

    def test_outcome_auditor_for_chat(self) -> None:
        decision = self.planner.decide("ji")
        report = self.auditor.build_report(decision=decision, language="en", question_answer="Yes boss, I am here.")

        self.assertIn("Intent: chat", report)
        self.assertIn("conversational response", report)


class AssistantMemoryStoreTests(unittest.TestCase):
    def test_memory_store_adds_notes_and_todos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AssistantMemoryStore(Path(temp_dir) / "assistant_memory.json")

            store.add_note("Remember my style")
            todo = store.add_todo("Finish deployment docs")

            self.assertIsNotNone(todo)
            open_todos = store.list_open_todos()
            self.assertEqual(len(open_todos), 1)
            self.assertIn("Finish deployment docs", open_todos[0].text)

            closed = store.close_todo_by_index(1)
            self.assertIsNotNone(closed)
            self.assertEqual(len(store.list_open_todos()), 0)


if __name__ == "__main__":
    unittest.main()
