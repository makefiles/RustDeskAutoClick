@echo off
REM RustDesk Auto-Accept — Windows Task Scheduler uninstaller

REM Request admin elevation if not already elevated
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

set TASK_NAME=RustDeskAutoAccept

echo Stopping running instances...
taskkill /f /im pythonw.exe >nul 2>&1
taskkill /f /im python.exe >nul 2>&1

echo Removing scheduled task...
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
if %errorlevel% equ 0 (
    echo Task "%TASK_NAME%" removed.
) else (
    echo Task "%TASK_NAME%" was not found. Nothing to uninstall.
)

echo.
echo === Uninstall complete ===
echo Config and script files are still in place - delete manually if needed.
pause
