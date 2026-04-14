"""Tests for P5-T2 memory ingestion adapters."""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from runtime.memory import (
    CommandHistoryIngestionAdapter,
    FileIngestionAdapter,
    IngestionError,
    LogIngestionAdapter,
    MemoryIngestionAdapters,
    NotesIngestionAdapter,
)


class FileIngestionAdapterTests(unittest.TestCase):
    def test_ingests_file_content_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "notes.txt"
            file_path.write_text("line one\nline two\n", encoding="utf-8")

            adapter = FileIngestionAdapter()
            document = adapter.ingest_file(file_path)

            self.assertEqual(document.source_type, "file")
            self.assertEqual(document.content, "line one line two")
            self.assertEqual(document.metadata["path"], str(file_path))
            self.assertTrue(document.content_hash)

    def test_missing_file_raises(self) -> None:
        adapter = FileIngestionAdapter()

        with self.assertRaises(IngestionError):
            adapter.ingest_file("C:/does/not/exist.txt")


class NotesIngestionAdapterTests(unittest.TestCase):
    def test_skips_empty_notes_and_keeps_index(self) -> None:
        adapter = NotesIngestionAdapter()
        docs = adapter.ingest_notes(["first note", "", "  ", "second note"], notebook_id="ops")

        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0].source_id, "ops:1")
        self.assertEqual(docs[1].source_id, "ops:4")


class LogIngestionAdapterTests(unittest.TestCase):
    def test_extracts_log_level_and_timestamp_hint(self) -> None:
        adapter = LogIngestionAdapter()
        docs = adapter.ingest_logs([
            "[2026-04-14T12:00:00Z] INFO service started",
            "WARN cache lag detected",
        ])

        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0].metadata["log_level"], "INFO")
        self.assertEqual(docs[0].metadata["timestamp_hint"], "2026-04-14T12:00:00Z")
        self.assertEqual(docs[1].metadata["log_level"], "WARN")


class CommandHistoryIngestionAdapterTests(unittest.TestCase):
    def test_normalizes_commands_and_skips_comments(self) -> None:
        adapter = CommandHistoryIngestionAdapter()
        docs = adapter.ingest_history(
            [
                "  ",
                "# this is a comment",
                "1 ls -la",
                "2   git   status",
                "python -m unittest",
            ],
            session_id="shell-main",
        )

        self.assertEqual(len(docs), 3)
        self.assertEqual(docs[0].content, "ls -la")
        self.assertEqual(docs[1].content, "git status")
        self.assertEqual(docs[2].content, "python -m unittest")


class MemoryIngestionAdaptersTests(unittest.TestCase):
    def test_composite_exposes_all_adapters(self) -> None:
        adapters = MemoryIngestionAdapters()

        self.assertIsInstance(adapters.files, FileIngestionAdapter)
        self.assertIsInstance(adapters.notes, NotesIngestionAdapter)
        self.assertIsInstance(adapters.logs, LogIngestionAdapter)
        self.assertIsInstance(adapters.command_history, CommandHistoryIngestionAdapter)


if __name__ == "__main__":
    unittest.main()
