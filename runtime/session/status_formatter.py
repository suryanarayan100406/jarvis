"""Status update formatter built on top of the session protocol contract."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .session_protocol import SessionProtocolContract


@dataclass(frozen=True)
class FormattedStatusUpdate:
    message: str
    state: str
    progress: int
    descriptor: str
    addressed_to: str | None
    task_id: str | None
    eta_seconds: int | None


class StatusUpdateFormatter:
    """Formats and validates status updates against the required protocol shape."""

    _status_regex = re.compile(r"\[STATUS: [A-Za-z ]+ \| [0-9]{1,3}%\] - .+")

    def __init__(self, contract: SessionProtocolContract | None = None) -> None:
        self.contract = contract

    def format_update(
        self,
        state: str,
        progress: int,
        descriptor: str,
        *,
        address: str | None = None,
        task_id: str | None = None,
        eta_seconds: int | None = None,
    ) -> FormattedStatusUpdate:
        normalized_state = " ".join(state.split())
        normalized_descriptor = " ".join(descriptor.split())
        normalized_address = " ".join(address.split()) if address else None
        normalized_task = " ".join(task_id.split()) if task_id else None

        if not normalized_state:
            raise ValueError("state is required")
        if progress < 0 or progress > 100:
            raise ValueError("progress must be between 0 and 100")
        if not normalized_descriptor:
            raise ValueError("descriptor is required")
        if eta_seconds is not None and eta_seconds < 0:
            raise ValueError("eta_seconds must be non-negative")

        if self.contract is not None:
            base_message = self.contract.format_status_update(normalized_state, progress, normalized_descriptor)
        else:
            base_message = f"[STATUS: {normalized_state} | {progress}%] - {normalized_descriptor}"

        if not self.validate(base_message):
            raise ValueError(f"Formatted status does not match required contract shape: {base_message}")

        final_message = f"{normalized_address}, {base_message}" if normalized_address else base_message

        suffix_parts: list[str] = []
        if normalized_task:
            suffix_parts.append(f"TASK: {normalized_task}")
        if eta_seconds is not None:
            suffix_parts.append(f"ETA: {eta_seconds}s")
        if suffix_parts:
            final_message = f"{final_message} ({'; '.join(suffix_parts)})"

        return FormattedStatusUpdate(
            message=final_message,
            state=normalized_state,
            progress=progress,
            descriptor=normalized_descriptor,
            addressed_to=normalized_address,
            task_id=normalized_task,
            eta_seconds=eta_seconds,
        )

    def validate(self, message: str) -> bool:
        return self._status_regex.search(message) is not None
