# Installation Guide

## 1. Clone the Repository
```bash
git clone https://github.com/suryanarayan100406/jarvis.git
cd jarvis
```

## 2. Create and Activate Virtual Environment
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

## 3. Install Dependencies
```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 4. Validate Installation
```bash
python -m unittest discover -s runtime/tests
```

If the test output ends with `OK`, installation is complete.

## 5. Optional CLI Smoke Test
```bash
python -m runtime.cli --help
python -m runtime.cli submit --goal "Install smoke check" --actor-id boss
python -m runtime.cli assistant --mode both --actor-id boss
```

Hindi-first assistant with startup briefing:

```bash
python -m runtime.cli assistant --mode both --actor-id boss --language hi
```

Disable startup weather/news briefing:

```bash
python -m runtime.cli assistant --mode both --actor-id boss --language hi --no-startup-brief
```

## 6. Optional Ollama Integration
If you want richer local conversational responses:

```bash
ollama pull qwen2.5:3b-instruct
python -m runtime.cli assistant --mode both --actor-id boss --language hi --llm-provider ollama --ollama-model qwen2.5:3b-instruct
```

If Ollama is not running, FRIDAY automatically falls back to deterministic replies.

## Common Setup Issues

### PowerShell script execution blocked
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Missing dependencies
```bash
python -m pip install -r requirements.txt --upgrade
```

### Rebuild environment
```bash
# delete .venv folder first, then
python -m venv .venv
python -m pip install -r requirements.txt
```
