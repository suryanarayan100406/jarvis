"""Lightweight persistent local memory for assistant preferences and todos."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class TodoItem:
    todo_id: str
    text: str
    done: bool
    created_at: str


class AssistantMemoryStore:
    """Simple JSON-backed memory store for user preferences and todo notes."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def get_preference(self, key: str, default: str = "") -> str:
        payload = self._read()
        preferences = payload.get("preferences", {})
        value = preferences.get(key, default)
        return str(value) if value is not None else default

    def set_preference(self, key: str, value: str) -> None:
        payload = self._read()
        payload.setdefault("preferences", {})[key] = value
        self._write(payload)

    def add_note(self, text: str) -> None:
        normalized = _normalize(text)
        if not normalized:
            return

        payload = self._read()
        notes = payload.setdefault("notes", [])
        notes.append(
            {
                "text": normalized,
                "created_at": _utc_now_iso(),
            }
        )
        payload["notes"] = notes[-200:]
        self._write(payload)

    def add_todo(self, text: str) -> TodoItem | None:
        normalized = _normalize(text)
        if not normalized:
            return None

        todo = {
            "todo_id": f"todo-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
            "text": normalized,
            "done": False,
            "created_at": _utc_now_iso(),
        }

        payload = self._read()
        todos = payload.setdefault("todos", [])
        todos.append(todo)
        payload["todos"] = todos[-500:]
        self._write(payload)
        return TodoItem(**todo)

    def list_open_todos(self, limit: int = 10) -> list[TodoItem]:
        payload = self._read()
        todos = payload.get("todos", [])
        open_todos = [todo for todo in todos if not bool(todo.get("done", False))]
        selected = open_todos[: max(1, int(limit))]
        return [TodoItem(**self._normalize_todo(item)) for item in selected]

    def close_todo_by_index(self, index: int) -> TodoItem | None:
        payload = self._read()
        todos = payload.get("todos", [])
        open_indices = [idx for idx, todo in enumerate(todos) if not bool(todo.get("done", False))]
        if index < 1 or index > len(open_indices):
            return None

        real_index = open_indices[index - 1]
        todos[real_index]["done"] = True
        payload["todos"] = todos
        self._write(payload)
        return TodoItem(**self._normalize_todo(todos[real_index]))

    def _read(self) -> dict[str, object]:
        if not self.path.is_file():
            return {"preferences": {}, "notes": [], "todos": []}

        try:
            text = self.path.read_text(encoding="utf-8")
            payload = json.loads(text)
            if isinstance(payload, dict):
                payload.setdefault("preferences", {})
                payload.setdefault("notes", [])
                payload.setdefault("todos", [])
                return payload
        except Exception:
            pass
        return {"preferences": {}, "notes": [], "todos": []}

    def _write(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    @staticmethod
    def _normalize_todo(item: dict[str, object]) -> dict[str, object]:
        return {
            "todo_id": str(item.get("todo_id", "todo-unknown")),
            "text": _normalize(str(item.get("text", ""))),
            "done": bool(item.get("done", False)),
            "created_at": str(item.get("created_at", _utc_now_iso())),
        }


def _normalize(value: str) -> str:
    return " ".join(str(value).split())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["AssistantMemoryStore", "TodoItem"]
