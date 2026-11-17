@echo off
REM Creates the ReelRelay desktop shortcut. Run this once.
setlocal
cd /d "%~dp0"

where powershell >nul 2>nul
if errorlevel 1 (
    echo.
    echo   ERROR: Windows PowerShell was not found on this computer.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create-desktop-shortcut.ps1"
exit /b %ERRORLEVEL%
