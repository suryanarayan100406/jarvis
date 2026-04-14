"""Tests for P4-T9 structured result aggregation and reporting."""

from __future__ import annotations

import unittest

from runtime.control_plane import (
    AggregatedControlPlaneReport,
    ControlPlaneResultReporter,
    HostOperationResult,
    ParallelExecutionResult,
)


class ControlPlaneResultReporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reporter = ControlPlaneResultReporter()

    def test_aggregate_produces_summary_and_host_entries(self) -> None:
        execution = self._execution_result(
            results=(
                HostOperationResult(
                    host_id="host-b",
                    operation="collect_status",
                    status="success",
                    result={
                        "adapter_name": "remote-ssh",
                        "transport": "remote",
                        "identity": "svc-app",
                        "result": {"status": "ok"},
                    },
                    error=None,
                ),
                HostOperationResult(
                    host_id="host-a",
                    operation="collect_status",
                    status="error",
                    result=None,
                    error="RuntimeError: timeout",
                ),
            ),
            succeeded=1,
            failed=1,
            skipped=0,
        )

        report = self.reporter.aggregate(execution)

        self.assertIsInstance(report, AggregatedControlPlaneReport)
        self.assertEqual(report.total_requests, 2)
        self.assertEqual(report.by_status["success"], 1)
        self.assertEqual(report.by_status["error"], 1)
        self.assertEqual(len(report.hosts), 2)
        self.assertEqual(report.hosts[0].host_id, "host-a")
        self.assertEqual(report.hosts[1].host_id, "host-b")

    def test_failures_include_only_error_entries(self) -> None:
        execution = self._execution_result(
            results=(
                HostOperationResult(
                    host_id="host-a",
                    operation="collect_status",
                    status="error",
                    result=None,
                    error="RuntimeError: denied",
                ),
                HostOperationResult(
                    host_id="host-b",
                    operation="collect_status",
                    status="skipped",
                    result=None,
                    error="Skipped due to stop_on_error",
                ),
            ),
            succeeded=0,
            failed=1,
            skipped=1,
        )

        report = self.reporter.aggregate(execution)

        self.assertEqual(len(report.failures), 1)
        self.assertEqual(report.failures[0].host_id, "host-a")

    def test_as_dict_contains_summary_hosts_and_failures(self) -> None:
        execution = self._execution_result(
            results=(
                HostOperationResult(
                    host_id="host-a",
                    operation="collect_status",
                    status="success",
                    result={
                        "adapter_name": "remote-ssh",
                        "transport": "remote",
                        "identity": "svc-app",
                        "result": {"status": "ok"},
                    },
                    error=None,
                ),
            ),
            succeeded=1,
            failed=0,
            skipped=0,
        )

        report = self.reporter.aggregate(execution)
        payload = self.reporter.as_dict(report)

        self.assertIn("summary", payload)
        self.assertEqual(payload["summary"]["succeeded"], 1)
        self.assertEqual(len(payload["hosts"]), 1)
        self.assertEqual(payload["hosts"][0]["host_id"], "host-a")

    def test_render_text_contains_summary_and_host_lines(self) -> None:
        execution = self._execution_result(
            results=(
                HostOperationResult(
                    host_id="host-a",
                    operation="collect_status",
                    status="success",
                    result={
                        "adapter_name": "remote-ssh",
                        "transport": "remote",
                        "identity": "svc-app",
                        "result": {"status": "ok"},
                    },
                    error=None,
                ),
                HostOperationResult(
                    host_id="host-b",
                    operation="restart_service",
                    status="error",
                    result=None,
                    error="RuntimeError: denied",
                ),
            ),
            succeeded=1,
            failed=1,
            skipped=0,
        )

        report = self.reporter.aggregate(execution)
        text = self.reporter.render_text(report)

        self.assertIn("[CONTROL-PLANE REPORT]", text)
        self.assertIn("Summary: total=2, success=1, failed=1, skipped=0", text)
        self.assertIn("- host-a | collect_status | success", text)
        self.assertIn("- host-b | restart_service | error", text)

    def test_empty_execution_is_supported(self) -> None:
        execution = self._execution_result(results=(), succeeded=0, failed=0, skipped=0)

        report = self.reporter.aggregate(execution)

        self.assertEqual(report.total_requests, 0)
        self.assertEqual(len(report.hosts), 0)
        self.assertEqual(len(report.failures), 0)

    @staticmethod
    def _execution_result(
        *,
        results: tuple[HostOperationResult, ...],
        succeeded: int,
        failed: int,
        skipped: int,
    ) -> ParallelExecutionResult:
        return ParallelExecutionResult(
            orchestration_id="orchestration-1",
            total_requests=len(results),
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            configured_max_concurrency=3,
            observed_max_concurrency=2,
            started_at="2026-04-14T10:00:00Z",
            finished_at="2026-04-14T10:00:01Z",
            results=results,
        )


if __name__ == "__main__":
    unittest.main()
