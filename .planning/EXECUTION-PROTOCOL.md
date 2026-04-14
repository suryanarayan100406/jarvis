# Execution Protocol

## Task Loop
1. Select next task from active phase plan.
2. Implement task scope only.
3. Run task-specific unit, integration, and adversarial checks as applicable.
4. Compare outcomes against task Done criteria.
5. If all checks pass, mark task complete.
6. Commit and push verified changes.
7. Continue to next task.

## Failure Handling
1. If any check fails, do not advance tasks.
2. Fix the failure and re-run checks.
3. Repeat until all checks pass.

## Git Cadence
1. Keep commits task-scoped and atomic.
2. Push after each verified task.
3. Use the configured remote repository as source of truth.

## Phase Completion Gate
1. A phase is complete only after all tasks pass verification.
2. Run full phase verification and adversarial checks before moving to the next phase.
