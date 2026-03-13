@echo off
REM RustDesk Auto-Accept — Windows Task Scheduler installer

REM Request admin elevation if not already elevated
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

set SCRIPT_DIR=%~dp0
set SCRIPT_PATH=%SCRIPT_DIR%rustdesk_autoclick.py
set TASK_NAME=RustDeskAutoAccept

if not exist "%SCRIPT_PATH%" (
    echo ERROR: rustdesk_autoclick.py not found at %SCRIPT_PATH%
    pause
    exit /b 1
)

if not exist "%SCRIPT_DIR%config.json" (
    echo ERROR: config.json not found at %SCRIPT_DIR%config.json
    pause
    exit /b 1
)

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    pause
    exit /b 1
)

REM Create scheduled task: run at logon, hidden window
schtasks /create /tn "%TASK_NAME%" /tr "pythonw \"%SCRIPT_PATH%\"" /sc onlogon /rl highest /f
if errorlevel 1 (
    echo ERROR: Failed to create scheduled task.
    pause
    exit /b 1
)

echo.
echo === Installation complete ===
echo Task "%TASK_NAME%" registered to run at logon.
echo.
echo Starting now...
start "" pythonw "%SCRIPT_PATH%"
echo.
echo To uninstall: run uninstall_windows.bat
pause
