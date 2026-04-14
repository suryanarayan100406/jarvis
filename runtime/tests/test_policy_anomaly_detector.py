"""Tests for P7-T7 policy anomaly detector."""

from __future__ import annotations

import unittest

from runtime.security import CommandPolicyEvent, PolicyAnomalyDetector


class PolicyAnomalyDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = PolicyAnomalyDetector(max_history_per_operator=20, deny_burst_window=4, deny_burst_threshold=3)

    def test_known_baseline_allow_is_low_risk(self) -> None:
        self.detector.register_baseline(
            operator_id="boss",
            operation="deploy",
            command="python deploy.py --env prod",
        )

        assessment = self.detector.analyze_event(
            CommandPolicyEvent(
                operator_id="boss",
                operation="deploy",
                command="python deploy.py --env prod",
                decision="allow",
                host_role="production",
            )
        )

        self.assertLess(assessment.anomaly_score, 0.25)
        self.assertEqual(assessment.risk_level, "low")
        self.assertFalse(assessment.should_escalate)

    def test_novel_command_pattern_is_medium_risk(self) -> None:
        assessment = self.detector.analyze_event(
            CommandPolicyEvent(
                operator_id="boss",
                operation="deploy",
                command="python deploy.py --env staging",
                decision="allow",
            )
        )

        signal_names = {signal.signal for signal in assessment.signals}
        self.assertIn("novel_command_pattern", signal_names)
        self.assertEqual(assessment.risk_level, "low")

    def test_privilege_escalation_and_dangerous_token_escalates(self) -> None:
        assessment = self.detector.analyze_event(
            CommandPolicyEvent(
                operator_id="operator-a",
                operation="shell",
                command="sudo bash -lc 'chmod 777 /etc/passwd && rm -rf /tmp/stage'",
                decision="deny",
                host_role="production",
            )
        )

        signal_names = {signal.signal for signal in assessment.signals}
        self.assertIn("privilege_escalation_pattern", signal_names)
        self.assertIn("dangerous_token_detected", signal_names)
        self.assertTrue(assessment.should_escalate)
        self.assertIn(assessment.risk_level, {"high", "critical"})
        self.assertEqual(assessment.recommended_action, "escalate_to_supervisor")

    def test_deny_burst_pattern_detected_within_window(self) -> None:
        for command in (
            "deploy service-a",
            "deploy service-b",
        ):
            self.detector.analyze_event(
                CommandPolicyEvent(
                    operator_id="operator-b",
                    operation="deploy",
                    command=command,
                    decision="deny",
                    host_role="production",
                )
            )

        assessment = self.detector.analyze_event(
            CommandPolicyEvent(
                operator_id="operator-b",
                operation="deploy",
                command="deploy service-c",
                decision="deny",
                host_role="production",
            )
        )

        signal_names = {signal.signal for signal in assessment.signals}
        self.assertIn("deny_burst_pattern", signal_names)
        self.assertFalse(assessment.should_escalate)
        self.assertEqual(assessment.recommended_action, "require_additional_review")

    def test_allow_event_adds_signature_to_operator_baseline(self) -> None:
        event = CommandPolicyEvent(
            operator_id="operator-c",
            operation="sync",
            command="python sync.py --target edge-1",
            decision="allow",
            host_role="edge",
        )

        first = self.detector.analyze_event(event)
        second = self.detector.analyze_event(event)

        first_signals = {signal.signal for signal in first.signals}
        second_signals = {signal.signal for signal in second.signals}
        self.assertIn("novel_command_pattern", first_signals)
        self.assertNotIn("novel_command_pattern", second_signals)


if __name__ == "__main__":
    unittest.main()
