"""Reporter stage for run summaries and artifact references."""

from __future__ import annotations

from uuid import uuid4

from runtime.pipeline.models import ExecutionResult, PlanResult, ReportResult, RunContext, ValidationResult


class ArtifactReporterStage:
    """Produces canonical run summaries and normalized artifact references."""

    def report(
        self,
        context: RunContext,
        plan: PlanResult,
        execution: ExecutionResult,
        validation: ValidationResult,
    ) -> ReportResult:
        artifacts = self._collect_artifacts(execution)
        summary = (
            f"run={context.run_id}; plan={plan.plan_id}; execution={execution.status}; "
            f"validation={validation.passed}; tasks={len(plan.tasks)}; artifacts={len(artifacts)}"
        )

        metadata = {
            "validation_passed": validation.passed,
            "task_count": len(plan.tasks),
            "artifact_count": len(artifacts),
            "execution_status": execution.status,
        }

        return ReportResult(
            report_id=str(uuid4()),
            summary=summary,
            artifacts=artifacts,
            metadata=metadata,
        )

    def _collect_artifacts(self, execution: ExecutionResult) -> list[dict[str, object]]:
        gathered: list[dict[str, object]] = []
        seen: set[str] = set()

        for artifact in execution.artifacts:
            key = self._artifact_key(artifact)
            if key not in seen:
                gathered.append(artifact)
                seen.add(key)

        for output in execution.outputs:
            artifact = output.get("artifact") if isinstance(output, dict) else None
            if isinstance(artifact, dict):
                key = self._artifact_key(artifact)
                if key not in seen:
                    gathered.append(artifact)
                    seen.add(key)

        return gathered

    def _artifact_key(self, artifact: dict[str, object]) -> str:
        artifact_id = str(artifact.get("artifact_id", ""))
        path = str(artifact.get("path", ""))
        uri = str(artifact.get("uri", ""))
        return f"{artifact_id}|{path}|{uri}"
