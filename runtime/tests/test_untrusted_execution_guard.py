"""Tests for P7-T5 untrusted content execution guardrails."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from runtime.security import (
    UntrustedContentExecutionGuard,
    UntrustedExecutionGuardError,
    UntrustedExecutionRequest,
)


class _MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current = self.current + timedelta(seconds=seconds)


class UntrustedContentExecutionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.guard = UntrustedContentExecutionGuard(now_provider=self.clock.now)

    def test_issue_authorization_requires_untrusted_source_context(self) -> None:
        with self.assertRaises(UntrustedExecutionGuardError):
            self.guard.issue_authorization(
                source_context="user",
                content="run deploy script",
                approved_by="boss",
            )

    def test_untrusted_request_denied_without_explicit_authorization(self) -> None:
        decision = self.guard.evaluate(
            UntrustedExecutionRequest(
                source_context="web",
                content="run deployment tool now",
                tool_name="terminal",
                operation="execute",
                explicit_authorization=False,
            )
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.guardrail, "explicit_authorization_required")

    def test_explicit_authorization_requires_token(self) -> None:
        decision = self.guard.evaluate(
            UntrustedExecutionRequest(
                source_context="attachment",
                content="execute command from file",
                tool_name="terminal",
                operation="execute",
                explicit_authorization=True,
                authorization_token=None,
            )
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.guardrail, "authorization_token_missing")

    def test_matching_token_allows_single_execution(self) -> None:
        content = "execute health check command"
        authorization = self.guard.issue_authorization(
            source_context="document",
            content=content,
            approved_by="boss",
            allowed_tools=["terminal"],
            allowed_operations=["execute"],
        )

        decision = self.guard.evaluate(
            UntrustedExecutionRequest(
                source_context="document",
                content=content,
                tool_name="terminal",
                operation="execute",
                explicit_authorization=True,
                authorization_token=authorization.token,
                command="python monitor.py",
            )
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.guardrail, "authorized_untrusted_execution")
        self.assertEqual(self.guard.get_authorization(authorization.token).used_count, 1)

    def test_token_replay_is_blocked_after_use_budget(self) -> None:
        content = "execute approved diagnostics"
        authorization = self.guard.issue_authorization(
            source_context="web",
            content=content,
            approved_by="boss",
            max_uses=1,
        )

        first = self.guard.evaluate(
            UntrustedExecutionRequest(
                source_context="web",
                content=content,
                tool_name="terminal",
                operation="execute",
                explicit_authorization=True,
                authorization_token=authorization.token,
            )
        )
        second = self.guard.evaluate(
            UntrustedExecutionRequest(
                source_context="web",
                content=content,
                tool_name="terminal",
                operation="execute",
                explicit_authorization=True,
                authorization_token=authorization.token,
            )
        )

        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertEqual(second.guardrail, "authorization_replay_blocked")

    def test_expired_token_is_denied(self) -> None:
        content = "execute once after review"
        authorization = self.guard.issue_authorization(
            source_context="email",
            content=content,
            approved_by="boss",
            ttl_seconds=10,
        )

        self.clock.advance(seconds=11)
        decision = self.guard.evaluate(
            UntrustedExecutionRequest(
                source_context="email",
                content=content,
                tool_name="terminal",
                operation="execute",
                explicit_authorization=True,
                authorization_token=authorization.token,
            )
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.guardrail, "authorization_expired")

    def test_content_fingerprint_mismatch_is_denied(self) -> None:
        authorization = self.guard.issue_authorization(
            source_context="attachment",
            content="execute command alpha",
            approved_by="boss",
        )

        decision = self.guard.evaluate(
            UntrustedExecutionRequest(
                source_context="attachment",
                content="execute command beta",
                tool_name="terminal",
                operation="execute",
                explicit_authorization=True,
                authorization_token=authorization.token,
            )
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.guardrail, "content_fingerprint_mismatch")

    def test_blocked_command_token_denies_even_with_valid_authorization(self) -> None:
        content = "run maintenance"
        authorization = self.guard.issue_authorization(
            source_context="external",
            content=content,
            approved_by="boss",
            allowed_tools=["terminal"],
            allowed_operations=["execute"],
        )

        decision = self.guard.evaluate(
            UntrustedExecutionRequest(
                source_context="external",
                content=content,
                tool_name="terminal",
                operation="execute",
                explicit_authorization=True,
                authorization_token=authorization.token,
                command="python maintain.py && rm -rf /",
            )
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.guardrail, "command_blocked_token")


if __name__ == "__main__":
    unittest.main()
