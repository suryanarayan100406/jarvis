"""Immutable audit writer with hash chaining for tamper detection."""

from __future__ import annotations

import json
import socket
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any


class ImmutableAuditWriter:
    """Append-only audit writer that links events using a hash chain."""

    def __init__(self, output_path: str | Path) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.chain_id, self._last_hash = self._load_chain_state()

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Append an audit event, returning the persisted event payload."""
        if not isinstance(event, dict):
            raise TypeError("Audit event must be a dictionary")
        if "event_type" not in event:
            raise ValueError("Audit event must include event_type")

        persisted = self._normalize_event(event)
        persisted["integrity"] = {
            "chain_id": self.chain_id,
            "prev_event_hash": self._last_hash,
            "event_hash": "",
        }

        event_hash = self._compute_event_hash(persisted)
        persisted["integrity"]["event_hash"] = event_hash

        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(persisted, sort_keys=True, separators=(",", ":")) + "\n")

        self._last_hash = event_hash
        return persisted

    def verify_chain(self) -> tuple[bool, list[str]]:
        """Verify event hash chain and return status plus issue list."""
        if not self.output_path.exists():
            return True, []

        issues: list[str] = []
        expected_prev: str | None = None
        expected_chain_id: str | None = None

        for line_number, raw_line in enumerate(self.output_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw_line.strip():
                continue

            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                issues.append(f"line {line_number}: invalid json")
                continue

            integrity = event.get("integrity", {})
            chain_id = integrity.get("chain_id")
            prev_hash = integrity.get("prev_event_hash")
            event_hash = integrity.get("event_hash")

            if expected_chain_id is None:
                expected_chain_id = chain_id
            elif chain_id != expected_chain_id:
                issues.append(f"line {line_number}: chain_id mismatch")

            if prev_hash != expected_prev:
                issues.append(f"line {line_number}: prev_event_hash mismatch")

            computed_hash = self._compute_event_hash(event)
            if event_hash != computed_hash:
                issues.append(f"line {line_number}: event_hash mismatch")

            expected_prev = event_hash

        return len(issues) == 0, issues

    def _load_chain_state(self) -> tuple[str, str | None]:
        if not self.output_path.exists():
            return str(uuid.uuid4()), None

        lines = [line for line in self.output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return str(uuid.uuid4()), None

        first_event = json.loads(lines[0])
        last_event = json.loads(lines[-1])
        chain_id = first_event.get("integrity", {}).get("chain_id") or str(uuid.uuid4())
        last_hash = last_event.get("integrity", {}).get("event_hash")
        return chain_id, last_hash

    def _normalize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(event)
        normalized.setdefault("schema_version", "1.0.0")
        normalized.setdefault("event_id", str(uuid.uuid4()))
        normalized.setdefault("timestamp", _utc_now_iso())
        normalized.setdefault("severity", "info")
        normalized.setdefault("source", {"component": "runtime", "host": socket.gethostname()})
        normalized.setdefault("payload", {})
        normalized.setdefault(
            "trace",
            {
                "trace_id": uuid.uuid4().hex,
                "span_id": uuid.uuid4().hex[:16],
            },
        )
        return normalized

    def _compute_event_hash(self, event: dict[str, Any]) -> str:
        hash_source = deepcopy(event)
        integrity = dict(hash_source.get("integrity", {}))
        integrity["event_hash"] = ""
        hash_source["integrity"] = integrity
        canonical = json.dumps(hash_source, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(canonical).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
