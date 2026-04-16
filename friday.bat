@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="" goto :launch_default

set "COMMAND=%~1"
shift

call :resolve_python
if errorlevel 1 exit /b 1

if /I "%COMMAND%"=="help" goto :usage
if /I "%COMMAND%"=="setup" goto :setup
if /I "%COMMAND%"=="test" goto :test
if /I "%COMMAND%"=="verify" goto :verify
if /I "%COMMAND%"=="cli-help" goto :cli_help
if /I "%COMMAND%"=="assistant" goto :assistant
if /I "%COMMAND%"=="submit" goto :submit
if /I "%COMMAND%"=="status" goto :status
if /I "%COMMAND%"=="replay" goto :replay
if /I "%COMMAND%"=="stop" goto :stop

echo [ERROR] Unknown command: %COMMAND%
echo.
goto :usage_error

:launch_default
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] First-time setup required.
    call :setup
    if errorlevel 1 exit /b 1
) else (
    call :resolve_python
    if errorlevel 1 exit /b 1
)

echo [INFO] Launching FRIDAY assistant...
"%PYTHON%" -m runtime.cli assistant --mode both --actor-id boss --language hi --llm-provider auto
exit /b %errorlevel%

:setup
echo [INFO] Setting up FRIDAY environment...
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment at .venv
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python was not found on PATH.
        exit /b 1
    )
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        exit /b 1
    )
    set "PYTHON=.venv\Scripts\python.exe"
)

echo [INFO] Using Python: %PYTHON%
"%PYTHON%" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip.
    exit /b 1
)
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    exit /b 1
)

echo [OK] Setup complete.
exit /b 0

:test
echo [INFO] Running full test suite...
"%PYTHON%" -m unittest discover -s runtime/tests
if errorlevel 1 (
    echo [ERROR] Tests failed.
    exit /b 1
)

echo [OK] All tests passed.
exit /b 0

:verify
call :setup
if errorlevel 1 exit /b 1
call :test
if errorlevel 1 exit /b 1
call :cli_help
if errorlevel 1 exit /b 1

echo [OK] Verification complete.
exit /b 0

:cli_help
"%PYTHON%" -m runtime.cli --help
exit /b %errorlevel%

:assistant
"%PYTHON%" -m runtime.cli assistant %*
exit /b %errorlevel%

:submit
if "%~1"=="" (
    echo [ERROR] submit requires a goal.
    echo Example: friday.bat submit "Generate system status summary" boss
    exit /b 1
)
set "GOAL=%~1"
set "ACTOR_ID=%~2"
if "%ACTOR_ID%"=="" set "ACTOR_ID=boss"

"%PYTHON%" -m runtime.cli submit --goal "%GOAL%" --actor-id "%ACTOR_ID%"
exit /b %errorlevel%

:status
if "%~1"=="" (
    echo [ERROR] status requires a run id.
    echo Example: friday.bat status 123e4567-e89b-12d3-a456-426614174000
    exit /b 1
)
"%PYTHON%" -m runtime.cli status --run-id "%~1"
exit /b %errorlevel%

:replay
if "%~1"=="" (
    echo [ERROR] replay requires a run id.
    echo Example: friday.bat replay 123e4567-e89b-12d3-a456-426614174000 25
    exit /b 1
)
set "RUN_ID=%~1"
set "LIMIT=%~2"
if "%LIMIT%"=="" set "LIMIT=25"

"%PYTHON%" -m runtime.cli replay --run-id "%RUN_ID%" --limit "%LIMIT%"
exit /b %errorlevel%

:stop
if "%~1"=="" (
    echo [ERROR] stop requires a run id.
    echo Example: friday.bat stop 123e4567-e89b-12d3-a456-426614174000 operator_request
    exit /b 1
)
set "RUN_ID=%~1"
set "REASON=%~2"
if "%REASON%"=="" set "REASON=operator_request"

"%PYTHON%" -m runtime.cli stop --run-id "%RUN_ID%" --reason "%REASON%"
exit /b %errorlevel%

:resolve_python
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
    exit /b 0
)

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found. Run "friday.bat setup" after installing Python 3.12+.
    exit /b 1
)
set "PYTHON=python"
exit /b 0

:usage
@echo.
@echo FRIDAY Windows Launcher
@echo.
@echo Usage:
@echo   friday.bat ^(no args - launches assistant^)
@echo   friday.bat setup
@echo   friday.bat test
@echo   friday.bat verify
@echo   friday.bat cli-help
@echo   friday.bat assistant [--mode text^|audio^|both] [--actor-id boss] [--language hi^|en]
@echo   friday.bat submit "^<goal^>" [actor_id]
@echo   friday.bat status "^<run_id^>"
@echo   friday.bat replay "^<run_id^>" [limit]
@echo   friday.bat stop "^<run_id^>" [reason]
@echo.
@echo Examples:
@echo   friday.bat verify
@echo   friday.bat assistant --mode both --actor-id boss --language hi --llm-provider auto
@echo   friday.bat submit "Generate system status summary" boss
@echo   friday.bat status "123e4567-e89b-12d3-a456-426614174000"
@echo.
exit /b 0

:usage_error
exit /b 1
