"""Tests for P2-T7 tool registry manifest loading and signature checks."""

from __future__ import annotations

import hmac
import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from runtime.registry import ToolManifestError, ToolRegistry


def _sign_payload(payload: dict, secret: str) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canonical, sha256).hexdigest()


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = "local-signing-secret"
        self.registry = ToolRegistry(keyring={"root": self.secret})

    def test_valid_manifest_loads(self) -> None:
        payload = {
            "tool_name": "terminal",
            "version": "1.0.0",
            "signing_key_id": "root",
            "capabilities": ["execute", "read_output"],
            "metadata": {"scope": "local"},
        }
        payload["signature"] = _sign_payload(payload, self.secret)

        manifest = self.registry.register_manifest(payload)

        self.assertEqual(manifest.tool_name, "terminal")
        self.assertEqual(len(self.registry.list_manifests()), 1)

    def test_unsigned_manifest_is_rejected(self) -> None:
        payload = {
            "tool_name": "filesystem",
            "version": "1.0.0",
            "signing_key_id": "root",
            "capabilities": ["read"],
        }

        with self.assertRaises(ToolManifestError):
            self.registry.register_manifest(payload)

    def test_invalid_signature_is_rejected(self) -> None:
        payload = {
            "tool_name": "network",
            "version": "1.0.0",
            "signing_key_id": "root",
            "capabilities": ["scan"],
            "signature": "deadbeef",
        }

        with self.assertRaises(ToolManifestError):
            self.registry.register_manifest(payload)

    def test_load_manifests_from_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)

            payload = {
                "tool_name": "service",
                "version": "1.0.0",
                "signing_key_id": "root",
                "capabilities": ["restart"],
                "metadata": {"scope": "host"},
            }
            payload["signature"] = _sign_payload(payload, self.secret)
            (directory / "service.json").write_text(json.dumps(payload), encoding="utf-8")

            loaded = self.registry.load_manifests(directory)

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].tool_name, "service")

    def test_duplicate_manifest_registration_is_rejected(self) -> None:
        payload = {
            "tool_name": "terminal",
            "version": "1.0.0",
            "signing_key_id": "root",
            "capabilities": ["execute"],
            "metadata": {},
        }
        payload["signature"] = _sign_payload(payload, self.secret)

        self.registry.register_manifest(payload)
        with self.assertRaises(ToolManifestError):
            self.registry.register_manifest(payload)


if __name__ == "__main__":
    unittest.main()
