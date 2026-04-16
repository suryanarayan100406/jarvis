# FRIDAY

Local-first autonomous assistant runtime inspired by FRIDAY and JARVIS, focused on deterministic orchestration, policy-gated execution, memory continuity, security controls, multimodal automation, and governance-first reliability.

## Project Status
- Milestone execution through Phase 12 is complete and verified.
- Governance and verification automation planning has started (Phases 13-17).
- Full regression result at current head: 813 tests passing.

## Prerequisites
- Python 3.12+
- Git
- PowerShell (Windows) or Bash-compatible shell (Linux/macOS)

## Installation

### 1. Clone
```powershell
git clone https://github.com/suryanarayan100406/jarvis.git
cd jarvis
```

### 2. Create virtual environment
Windows PowerShell:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Linux/macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies
```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Quick Verification
Run the full test suite:
```bash
python -m unittest discover -s runtime/tests
```

Expected pattern:
- A large sequence of `.` characters
- `Ran N tests in ...`
- Final `OK`

## CLI Quick Start
FRIDAY includes an operator CLI:
```bash
python -m runtime.cli --help
```

Subcommands:
- `submit` submit a goal and run deterministic orchestration
- `status` inspect run state and recent events
- `replay` replay run history for debugging and audit
- `stop` cancel an active run

Example flow:
```bash
python -m runtime.cli submit --goal "Generate system status summary" --actor-id boss
python -m runtime.cli status --run-id <RUN_ID>
python -m runtime.cli replay --run-id <RUN_ID> --limit 25
python -m runtime.cli stop --run-id <RUN_ID> --reason operator_request
```

Interactive assistant mode:
```bash
python -m runtime.cli assistant --mode both --actor-id boss
```

Assistant mode options:
- `--mode text` text-only interactive session.
- `--mode audio` voice-only interaction (Windows).
- `--mode both` text session with optional `/listen` voice capture and spoken responses.
- `--prompt "..."` single-turn assistant execution without opening an interactive loop.
- `--show-metadata` show run and status metadata after each response.

Interactive assistant commands:
- `/help` show assistant commands.
- `/listen` capture one voice input in `--mode both`.
- `/last` show the previous run metadata (`run_id`, `status`, `plan_id`).
- `/exit` close assistant mode.

Default run store path:
- `runtime/data/runs.db`

Windows launcher shortcut:
```powershell
friday.bat assistant --mode both --actor-id boss
```

## Feature Overview

### Core orchestration and runtime
- Deterministic planner/executor/validator/reporter pipeline
- Persistent run store and replay endpoint
- Operator CLI for run lifecycle control

### Memory and continuity
- Short-term, long-term, and preference-aware memory handling
- Open-loop task register and status-check summaries
- User-correctable memory updates

### Policy, safety, and security
- Risk-tier policy engine and approval routing
- Kill-switch and untrusted instruction guards
- Prompt injection, identity override, anomaly, and social-engineering protections
- Tamper-evident audit and forensic export support

### Persona and communication
- FRIDAY/JARVIS profile behavior and addressing controls
- Answer-first response formatting and confidence tagging
- Startup boot and session carry-over workflows
- Directive compliance, drift detection, correction, and audit reporting

### Multimodal and control surfaces
- Screenshot/OCR/UI grounding and safe action execution
- Local/remote control-plane orchestration
- Optional physical connector safety stack

### Reliability and launch governance
- SLO/error-budget monitoring and alert routing
- Backup/restore/disaster-recovery workflows
- Launch readiness and release controls

## Documentation Index
- Testing guide: `docs/TESTING.md`
- Feature map: `docs/FEATURES.md`
- Architecture decisions: `docs/adr/`
- Contract schemas: `contracts/schemas/v1/README.md`
- Planning artifacts: `.planning/`

## Troubleshooting

### Virtual environment activation blocked (Windows)
If PowerShell blocks script execution, run:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```
Then reactivate:
```powershell
.\.venv\Scripts\Activate.ps1
```

### Import/module errors
- Ensure virtual environment is active.
- Reinstall dependencies:
```bash
python -m pip install -r requirements.txt --upgrade
```

### Test failures after pulling updates
- Re-run full tests.
- Recreate venv if dependency drift is suspected.
```bash
rm -rf .venv  # Linux/macOS
# or delete .venv directory manually on Windows
python -m venv .venv
python -m pip install -r requirements.txt
```

## License and Contribution
This repository currently focuses on local development and governance-driven iteration. If you want contributor guidance and release policy docs, add a `CONTRIBUTING.md` and project license file in a future milestone.
