"""Ingestion adapters for files, notes, logs, and command history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IngestedDocument:
    document_id: str
    source_type: str
    source_id: str
    content: str
    content_hash: str
    metadata: dict[str, Any]
    ingested_at: str


class IngestionError(ValueError):
    """Raised when ingestion inputs are invalid or unreadable."""


class FileIngestionAdapter:
    """Ingests text files into normalized document records."""

    def ingest_file(
        self,
        file_path: str | Path,
        *,
        source_id: str | None = None,
        encoding: str = "utf-8",
        max_bytes: int | None = None,
    ) -> IngestedDocument:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise IngestionError(f"File does not exist: {path}")

        if max_bytes is not None and max_bytes < 1:
            raise IngestionError("max_bytes must be at least 1 when provided")

        raw = path.read_bytes()
        if max_bytes is not None:
            raw = raw[:max_bytes]

        content = raw.decode(encoding, errors="replace")
        normalized_content = _normalize_content(content)
        normalized_source_id = _normalize_required(source_id or str(path), "source_id")
        ingested_at = _utc_now_iso()

        return IngestedDocument(
            document_id=_hash_text(f"file:{normalized_source_id}:{normalized_content}"),
            source_type="file",
            source_id=normalized_source_id,
            content=normalized_content,
            content_hash=_hash_text(normalized_content),
            metadata={
                "path": str(path),
                "size_bytes": len(raw),
                "encoding": encoding,
            },
            ingested_at=ingested_at,
        )


class NotesIngestionAdapter:
    """Ingests note entries into memory-ready documents."""

    def ingest_notes(
        self,
        notes: list[str] | tuple[str, ...],
        *,
        notebook_id: str = "notes",
    ) -> list[IngestedDocument]:
        normalized_notebook = _normalize_required(notebook_id, "notebook_id")
        documents: list[IngestedDocument] = []

        for index, note in enumerate(notes):
            normalized_note = _normalize_content(note)
            if not normalized_note:
                continue

            source_id = f"{normalized_notebook}:{index + 1}"
            documents.append(
                IngestedDocument(
                    document_id=_hash_text(f"note:{source_id}:{normalized_note}"),
                    source_type="note",
                    source_id=source_id,
                    content=normalized_note,
                    content_hash=_hash_text(normalized_note),
                    metadata={
                        "notebook_id": normalized_notebook,
                        "note_index": index + 1,
                    },
                    ingested_at=_utc_now_iso(),
                )
            )

        return documents


class LogIngestionAdapter:
    """Ingests log lines with basic level and timestamp extraction."""

    def ingest_logs(
        self,
        lines: list[str] | tuple[str, ...],
        *,
        source_id: str = "log",
    ) -> list[IngestedDocument]:
        normalized_source = _normalize_required(source_id, "source_id")
        documents: list[IngestedDocument] = []

        for index, line in enumerate(lines):
            normalized_line = _normalize_content(line)
            if not normalized_line:
                continue

            metadata = {
                "line_number": index + 1,
                "log_level": _extract_log_level(normalized_line),
                "timestamp_hint": _extract_timestamp_hint(normalized_line),
            }
            line_source = f"{normalized_source}:{index + 1}"
            documents.append(
                IngestedDocument(
                    document_id=_hash_text(f"log:{line_source}:{normalized_line}"),
                    source_type="log",
                    source_id=line_source,
                    content=normalized_line,
                    content_hash=_hash_text(normalized_line),
                    metadata=metadata,
                    ingested_at=_utc_now_iso(),
                )
            )

        return documents


class CommandHistoryIngestionAdapter:
    """Ingests shell command history entries into normalized memory documents."""

    def ingest_history(
        self,
        commands: list[str] | tuple[str, ...],
        *,
        session_id: str = "shell",
    ) -> list[IngestedDocument]:
        normalized_session = _normalize_required(session_id, "session_id")
        documents: list[IngestedDocument] = []

        for index, command in enumerate(commands):
            normalized_command = _normalize_content(command)
            if not normalized_command:
                continue
            if normalized_command.startswith("#"):
                continue

            normalized_command = _strip_history_prefix(normalized_command)
            if not normalized_command:
                continue

            source_id = f"{normalized_session}:{index + 1}"
            documents.append(
                IngestedDocument(
                    document_id=_hash_text(f"cmd:{source_id}:{normalized_command}"),
                    source_type="command_history",
                    source_id=source_id,
                    content=normalized_command,
                    content_hash=_hash_text(normalized_command),
                    metadata={
                        "session_id": normalized_session,
                        "command_index": index + 1,
                    },
                    ingested_at=_utc_now_iso(),
                )
            )

        return documents


class MemoryIngestionAdapters:
    """Composite entry point exposing all ingestion adapter types."""

    def __init__(self) -> None:
        self.files = FileIngestionAdapter()
        self.notes = NotesIngestionAdapter()
        self.logs = LogIngestionAdapter()
        self.command_history = CommandHistoryIngestionAdapter()


def _extract_log_level(line: str) -> str | None:
    upper = line.upper()
    for marker in ("DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL"):
        if marker in upper:
            return "WARN" if marker == "WARNING" else marker
    return None


def _extract_timestamp_hint(line: str) -> str | None:
    if line.startswith("[") and "]" in line:
        candidate = line[1 : line.index("]")]
        normalized = candidate.strip()
        return normalized or None
    return None


def _strip_history_prefix(command: str) -> str:
    parts = command.split(" ", 1)
    if len(parts) == 2 and parts[0].isdigit():
        return _normalize_content(parts[1])
    return command


def _normalize_content(content: str) -> str:
    return " ".join(content.split())


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise IngestionError(f"{field_name} is required")
    return normalized


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
