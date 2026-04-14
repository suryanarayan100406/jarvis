"""SSH-based remote connector with per-host key isolation controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .host_inventory import HostRecord


class SshTransport(Protocol):
    def run(self, request: "SshExecutionRequest") -> dict[str, Any]:
        """Execute an SSH request and return a structured result payload."""


@dataclass(frozen=True)
class SshHostCredentials:
    host_id: str
    username: str
    private_key_ref: str
    port: int
    known_host_fingerprint: str | None
    allowed_operations: tuple[str, ...]
    updated_at: str


@dataclass(frozen=True)
class SshExecutionRequest:
    host_id: str
    hostname: str
    address: str
    username: str
    private_key_ref: str
    port: int
    known_host_fingerprint: str | None
    operation: str
    command: str
    payload: dict[str, Any]
    identity: str | None


class SshConnectorError(ValueError):
    """Raised when SSH connector configuration or execution violates constraints."""


class SshRemoteConnector:
    """SSH connector enforcing host-bound keys and operation-level scoping."""

    def __init__(self, transport: SshTransport | None = None) -> None:
        self.transport = transport or _NoopSshTransport()
        self._credentials_by_host: dict[str, SshHostCredentials] = {}
        self._key_owner: dict[str, str] = {}

    def configure_host_key(
        self,
        *,
        host_id: str,
        username: str,
        private_key_ref: str,
        port: int = 22,
        known_host_fingerprint: str | None = None,
        allowed_operations: list[str] | tuple[str, ...] | None = None,
    ) -> SshHostCredentials:
        normalized_host_id = _normalize_required(host_id, "host_id")
        normalized_username = _normalize_required(username, "username")
        normalized_key_ref = _normalize_required(private_key_ref, "private_key_ref")

        if port < 1 or port > 65535:
            raise SshConnectorError("port must be between 1 and 65535")

        normalized_fingerprint = (
            _normalize_required(known_host_fingerprint, "known_host_fingerprint")
            if known_host_fingerprint is not None
            else None
        )
        normalized_operations = _normalize_operations(allowed_operations)

        existing_owner = self._key_owner.get(normalized_key_ref)
        if existing_owner is not None and existing_owner != normalized_host_id:
            raise SshConnectorError(
                "private_key_ref is already assigned to another host; key isolation violation"
            )

        previous = self._credentials_by_host.get(normalized_host_id)
        if previous is not None and previous.private_key_ref != normalized_key_ref:
            self._key_owner.pop(previous.private_key_ref, None)

        credentials = SshHostCredentials(
            host_id=normalized_host_id,
            username=normalized_username,
            private_key_ref=normalized_key_ref,
            port=port,
            known_host_fingerprint=normalized_fingerprint,
            allowed_operations=normalized_operations,
            updated_at=_utc_now_iso(),
        )
        self._credentials_by_host[normalized_host_id] = credentials
        self._key_owner[normalized_key_ref] = normalized_host_id
        return credentials

    def get_host_key(self, host_id: str) -> SshHostCredentials:
        normalized_host_id = _normalize_required(host_id, "host_id")
        credentials = self._credentials_by_host.get(normalized_host_id)
        if credentials is None:
            raise KeyError(f"No SSH credentials configured for host: {normalized_host_id}")
        return credentials

    def remove_host_key(self, host_id: str) -> None:
        credentials = self.get_host_key(host_id)
        self._credentials_by_host.pop(credentials.host_id, None)
        self._key_owner.pop(credentials.private_key_ref, None)

    def execute(
        self,
        *,
        host: HostRecord,
        operation: str,
        payload: dict[str, Any],
        identity: str | None = None,
    ) -> dict[str, Any]:
        if host.role == "local":
            raise SshConnectorError("SSH connector cannot execute against local role hosts")

        credentials = self.get_host_key(host.host_id)
        normalized_operation = _normalize_required(operation, "operation").lower()
        request_payload = dict(payload)

        if request_payload.get("host_id") is not None:
            payload_host_id = _normalize_required(str(request_payload["host_id"]), "payload.host_id")
            if payload_host_id != host.host_id:
                raise SshConnectorError(
                    "payload.host_id does not match target host_id; cross-host credential replay blocked"
                )

        if credentials.allowed_operations and normalized_operation not in credentials.allowed_operations:
            allowed = ", ".join(credentials.allowed_operations)
            raise SshConnectorError(
                f"Operation {normalized_operation} is not allowed for host {host.hostname}. Allowed: {allowed}"
            )

        command = _extract_command(request_payload)
        request = SshExecutionRequest(
            host_id=host.host_id,
            hostname=host.hostname,
            address=host.address,
            username=credentials.username,
            private_key_ref=credentials.private_key_ref,
            port=credentials.port,
            known_host_fingerprint=credentials.known_host_fingerprint,
            operation=normalized_operation,
            command=command,
            payload=request_payload,
            identity=_normalize_required(identity, "identity") if identity is not None else None,
        )

        result = self.transport.run(request)
        if not isinstance(result, dict):
            raise SshConnectorError(f"SSH transport returned invalid result type: {type(result).__name__}")

        return {
            "transport": "ssh",
            "host_id": host.host_id,
            "hostname": host.hostname,
            "operation": normalized_operation,
            "identity": request.identity,
            "username": credentials.username,
            "port": credentials.port,
            "key_ref": credentials.private_key_ref,
            "result": dict(result),
        }


class _NoopSshTransport:
    def run(self, request: SshExecutionRequest) -> dict[str, Any]:
        return {
            "status": "simulated",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "command": request.command,
        }


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise SshConnectorError(f"{field_name} is required")
    return normalized


def _normalize_operations(operations: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if operations is None:
        return ()
    normalized = sorted({_normalize_required(operation, "allowed_operation").lower() for operation in operations})
    return tuple(normalized)


def _extract_command(payload: dict[str, Any]) -> str:
    if "command" not in payload:
        raise SshConnectorError("payload.command is required")

    command = payload["command"]
    if not isinstance(command, str):
        raise SshConnectorError("payload.command must be a string")
    if any(char in command for char in ("\n", "\r", "\x00")):
        raise SshConnectorError("payload.command contains disallowed control characters")

    return _normalize_required(command, "payload.command")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
