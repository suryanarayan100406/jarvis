"""Tool registry with manifest loading and signature verification."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any


class ToolManifestError(ValueError):
    """Raised when tool manifest parsing or validation fails."""


@dataclass(frozen=True)
class ToolManifest:
    tool_name: str
    version: str
    signing_key_id: str
    capabilities: list[str]
    metadata: dict[str, Any]
    signature: str


class ToolRegistry:
    """Loads and validates signed tool manifests into an in-memory registry."""

    def __init__(self, keyring: dict[str, str]) -> None:
        if not keyring:
            raise ValueError("Keyring is required for signature verification")
        self.keyring = keyring
        self._tools: dict[str, ToolManifest] = {}

    def load_manifests(self, manifest_dir: str | Path) -> list[ToolManifest]:
        directory = Path(manifest_dir)
        if not directory.exists():
            raise FileNotFoundError(f"Manifest directory does not exist: {directory}")

        loaded: list[ToolManifest] = []
        for path in sorted(directory.glob("*.json")):
            loaded.append(self.register_manifest_file(path))
        return loaded

    def register_manifest_file(self, manifest_path: str | Path) -> ToolManifest:
        path = Path(manifest_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return self.register_manifest(payload)

    def register_manifest(self, payload: dict[str, Any]) -> ToolManifest:
        manifest = self._parse_manifest(payload)
        self._verify_signature(payload)

        key = f"{manifest.tool_name}@{manifest.version}"
        if key in self._tools:
            raise ToolManifestError(f"Duplicate manifest registration for {key}")

        self._tools[key] = manifest
        return manifest

    def get_manifest(self, tool_name: str, version: str) -> ToolManifest:
        key = f"{tool_name}@{version}"
        if key not in self._tools:
            raise KeyError(f"Manifest not found: {key}")
        return self._tools[key]

    def list_manifests(self) -> list[ToolManifest]:
        return list(self._tools.values())

    def _parse_manifest(self, payload: dict[str, Any]) -> ToolManifest:
        required = ["tool_name", "version", "signing_key_id", "capabilities", "signature"]
        missing = [field for field in required if field not in payload]
        if missing:
            raise ToolManifestError(f"Manifest missing required fields: {', '.join(missing)}")

        if not isinstance(payload["capabilities"], list) or not payload["capabilities"]:
            raise ToolManifestError("Manifest capabilities must be a non-empty list")

        return ToolManifest(
            tool_name=str(payload["tool_name"]),
            version=str(payload["version"]),
            signing_key_id=str(payload["signing_key_id"]),
            capabilities=[str(item) for item in payload["capabilities"]],
            metadata=dict(payload.get("metadata", {})),
            signature=str(payload["signature"]),
        )

    def _verify_signature(self, payload: dict[str, Any]) -> None:
        key_id = payload.get("signing_key_id")
        signature = payload.get("signature")
        if not signature:
            raise ToolManifestError("Manifest must include signature")
        if key_id not in self.keyring:
            raise ToolManifestError(f"Unknown signing key id: {key_id}")

        payload_to_sign = dict(payload)
        payload_to_sign.pop("signature", None)
        canonical = json.dumps(payload_to_sign, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = hmac.new(self.keyring[key_id].encode("utf-8"), canonical, sha256).hexdigest()

        if not hmac.compare_digest(expected, str(signature)):
            raise ToolManifestError("Invalid manifest signature")
