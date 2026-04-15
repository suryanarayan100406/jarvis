# Testing Guide

This document explains how to validate FRIDAY locally, from quick checks to full regression.

## Test Framework
- Python built-in `unittest`
- Test root: `runtime/tests`
- Naming: `test_*.py`

## Prerequisites
1. Activate your virtual environment.
2. Install dependencies from `requirements.txt`.

## Run All Tests
```bash
python -m unittest discover -s runtime/tests
```

## Run a Specific Module
```bash
python -m unittest runtime.tests.test_phase_summary_contract
```

## Run Multiple Targeted Modules
```bash
python -m unittest runtime.tests.test_phase_summary_contract runtime.tests.test_directive_audit
```

## Suggested Validation Sequence
1. Run targeted tests for changed files.
2. Run full suite before commit.
3. Run full suite again after rebasing or large merges.

## Interpreting Results
Success output pattern:
- Dot stream for passing tests
- `Ran <count> tests in <time>s`
- `OK`

Failure output pattern:
- `FAIL` or `ERROR`
- Stack trace with module/class/test method

## High-Value Test Domains
- `test_policy_*`: policy and risk enforcement
- `test_security_*`: security controls and adversarial checks
- `test_memory_*`: memory correctness and continuity
- `test_mode_*`, `test_persona_*`, `test_directive_*`: persona and directive compliance
- `test_physical_*`: optional physical integration controls
- `test_visual_*`, `test_ui_*`: multimodal and UI automation safety
- `test_launch_*`, `test_release_*`, `test_disaster_*`: production reliability gates

## Regression Hygiene
Before pushing:
1. Confirm full suite passes.
2. Confirm no untracked generated artifacts are accidentally staged.
3. Confirm planning state/log updates match implemented task scope.

## CLI Smoke Checks (Optional)
After unit tests pass, perform a runtime smoke check:
```bash
python -m runtime.cli submit --goal "Smoke check" --actor-id boss
python -m runtime.cli status --run-id <RUN_ID>
python -m runtime.cli replay --run-id <RUN_ID> --limit 10
```
