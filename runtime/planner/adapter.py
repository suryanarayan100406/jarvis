"""Planner interface adapter with deterministic plan serialization."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from runtime.pipeline.models import PlanResult, PlannedTask, RunContext


class PlannerInterfaceAdapter:
    """Builds deterministic plans and canonical serialized payloads."""

    serializer_version = "deterministic-v1"

    def plan(self, context: RunContext) -> PlanResult:
        payload = self.build_plan_payload(goal=context.goal)
        tasks = [
            PlannedTask(
                task_id=task["task_id"],
                description=task["description"],
                metadata={"depends_on": task["depends_on"]},
            )
            for task in payload["tasks"]
        ]

        return PlanResult(
            plan_id=payload["plan_id"],
            tasks=tasks,
            metadata={
                "serialized_plan": payload["serialized_plan"],
                "serializer": self.serializer_version,
            },
        )

    def build_plan_payload(self, goal: str, constraints: dict[str, Any] | None = None) -> dict[str, Any]:
        if not goal or not goal.strip():
            raise ValueError("Goal is required for planning")

        normalized_goal = " ".join(goal.strip().split())
        normalized_constraints = self._normalize_constraints(constraints or {})
        steps = self._extract_steps(normalized_goal)

        tasks = []
        for index, step in enumerate(steps, start=1):
            digest = sha256(step.encode("utf-8")).hexdigest()[:8]
            tasks.append(
                {
                    "task_id": f"S{index:03d}-{digest}",
                    "order": index,
                    "description": step,
                    "depends_on": [tasks[index - 2]["task_id"]] if index > 1 else [],
                }
            )

        canonical_body = {
            "goal": normalized_goal,
            "constraints": normalized_constraints,
            "tasks": tasks,
        }
        serialized_plan = self.serialize(canonical_body)
        plan_id = sha256(serialized_plan.encode("utf-8")).hexdigest()[:16]

        return {
            "plan_id": plan_id,
            "goal": normalized_goal,
            "constraints": normalized_constraints,
            "tasks": tasks,
            "serialized_plan": serialized_plan,
        }

    def serialize(self, payload: dict[str, Any]) -> str:
        """Return canonical JSON serialization with deterministic ordering."""
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _extract_steps(self, goal: str) -> list[str]:
        separators = [";", " and "]
        chunks = [goal]
        for separator in separators:
            if any(separator in chunk for chunk in chunks):
                next_chunks: list[str] = []
                for chunk in chunks:
                    next_chunks.extend(chunk.split(separator))
                chunks = next_chunks

        steps = [" ".join(chunk.strip().split()) for chunk in chunks if chunk.strip()]
        return steps or [goal]

    def _normalize_constraints(self, constraints: dict[str, Any]) -> dict[str, Any]:
        # Round-trip through sorted JSON to guarantee deterministic key ordering.
        return json.loads(json.dumps(constraints, sort_keys=True, separators=(",", ":")))
