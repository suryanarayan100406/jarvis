"""Tests for P3-T10 mission brief renderer."""

from __future__ import annotations

import json
import unittest

from runtime.persona import MissionBriefRenderer, MissionBriefValidationError


class MissionBriefRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = MissionBriefRenderer()

    def test_build_brief_creates_schema_valid_payload(self) -> None:
        brief = self.renderer.build_brief(
            title="Secure perimeter",
            objective="Stabilize perimeter security",
            context="Unexpected sensor activity detected",
            tasks=["Scan all sectors", "Lock external access points"],
            constraints=["No external network access"],
            risks=["False positives may delay response"],
            status="In Progress",
            priority="CRITICAL",
            owner="Boss",
        )

        self.renderer.validate_brief(brief)
        self.assertEqual(brief["tasks"][0]["step_id"], "S001")
        self.assertEqual(brief["priority"], "CRITICAL")

    def test_render_markdown_contains_required_sections(self) -> None:
        brief = self.renderer.build_brief(
            title="Mission one",
            objective="Collect diagnostics",
            context="Routine health check",
            tasks=["Capture logs"],
        )

        text = self.renderer.render_markdown(brief)

        self.assertIn("# Mission Brief: Mission one", text)
        self.assertIn("## Objective", text)
        self.assertIn("## Execution Steps", text)
        self.assertIn("1. [pending] Capture logs", text)

    def test_render_json_is_parseable(self) -> None:
        brief = self.renderer.build_brief(
            title="Mission json",
            objective="Render as JSON",
            context="Serialization validation",
            tasks=["Serialize payload"],
        )

        payload = self.renderer.render_json(brief)
        parsed = json.loads(payload)

        self.assertEqual(parsed["title"], "Mission json")
        self.assertEqual(parsed["tasks"][0]["step_id"], "S001")

    def test_validate_brief_rejects_missing_required_field(self) -> None:
        invalid = {
            "mission_id": "m-1",
            "title": "Incomplete",
        }

        with self.assertRaises(MissionBriefValidationError):
            self.renderer.validate_brief(invalid)

    def test_render_rejects_unknown_format(self) -> None:
        brief = self.renderer.build_brief(
            title="Mission format",
            objective="Check format",
            context="Formatter guard",
            tasks=["Run check"],
        )

        with self.assertRaises(ValueError):
            self.renderer.render(brief, output_format="html")

    def test_task_description_is_required(self) -> None:
        with self.assertRaises(ValueError):
            self.renderer.build_brief(
                title="Mission invalid task",
                objective="Test task validation",
                context="Blank task",
                tasks=["   "],
            )


if __name__ == "__main__":
    unittest.main()
