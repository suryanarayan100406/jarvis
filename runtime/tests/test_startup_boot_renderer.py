"""Tests for P12-T1 startup boot renderer and integration state reporting."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from runtime.session import (
    IntegrationStateRecord,
    SessionProtocolContract,
    StartupBootRenderError,
    StartupBootRenderer,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "schemas" / "v1" / "session-protocol.schema.json"
EXAMPLE_PATH = ROOT / "contracts" / "schemas" / "v1" / "examples" / "session-protocol.example.json"


class StartupBootRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        config = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        self.contract = SessionProtocolContract(SCHEMA_PATH, config)
        self.renderer = StartupBootRenderer(contract=self.contract)

    def test_render_startup_with_contract_includes_boot_and_integration_summary(self) -> None:
        report = self.renderer.render_startup(
            [
                IntegrationStateRecord(
                    system_id="server-1",
                    status="online",
                    latency_ms=12.2,
                    last_checked_at="2026-04-15T00:00:00Z",
                    detail="reachable",
                    metadata={},
                ),
                IntegrationStateRecord(
                    system_id="laptop",
                    status="degraded",
                    latency_ms=88.0,
                    last_checked_at="2026-04-15T00:00:00Z",
                    detail="high latency",
                    metadata={},
                ),
            ],
            address="Boss",
            context_summary="resuming P12-T1",
        )

        self.assertEqual(report.overall_status, "degraded")
        self.assertEqual(report.online_count, 1)
        self.assertEqual(report.degraded_count, 1)
        self.assertEqual(report.offline_count, 0)
        self.assertIn("FRIDAY online. Running system check...", report.message)
        self.assertIn("Ready, Boss. What are we working on?", report.message)
        self.assertIn("Integration state: degraded", report.message)
        self.assertIn("- laptop: degraded", report.message)

    def test_render_startup_without_contract_uses_fallback_template(self) -> None:
        renderer = StartupBootRenderer()

        report = renderer.render_startup(
            [
                IntegrationStateRecord(
                    system_id="server-2",
                    status="online",
                    latency_ms=10.0,
                    last_checked_at="2026-04-15T00:00:00Z",
                    detail=None,
                    metadata={},
                )
            ],
            address="Boss",
            context_summary="none",
        )

        self.assertEqual(report.overall_status, "healthy")
        self.assertIn("FRIDAY online. Running system check...", report.message)
        self.assertIn("Ready, Boss. What are we working on?", report.message)
        self.assertIn("Integration state: healthy", report.message)

    def test_render_startup_marks_offline_as_critical(self) -> None:
        report = self.renderer.render_startup(
            [
                IntegrationStateRecord(
                    system_id="db",
                    status="offline",
                    latency_ms=None,
                    last_checked_at="2026-04-15T00:00:00Z",
                    detail="unreachable",
                    metadata={},
                )
            ],
            address="Boss",
            context_summary="startup",
        )

        self.assertEqual(report.overall_status, "critical")
        self.assertEqual(report.offline_count, 1)

    def test_invalid_status_raises(self) -> None:
        with self.assertRaises(StartupBootRenderError):
            self.renderer.render_startup(
                [
                    IntegrationStateRecord(
                        system_id="db",
                        status="broken",
                        latency_ms=1.0,
                        last_checked_at="2026-04-15T00:00:00Z",
                        detail=None,
                        metadata={},
                    )
                ],
                address="Boss",
                context_summary="startup",
            )

    def test_manifest_is_deterministic(self) -> None:
        report = self.renderer.render_startup(
            [
                IntegrationStateRecord(
                    system_id="cache",
                    status="online",
                    latency_ms=2.0,
                    last_checked_at="2026-04-15T00:00:00Z",
                    detail=None,
                    metadata={},
                )
            ],
            address="Boss",
            context_summary="startup",
        )

        first = report.to_manifest()
        second = report.to_manifest()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
