@echo off
REM ReelRelay launcher. Double-click this file or use the desktop shortcut.
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

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-reelrelay.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo   ReelRelay exited with code %EXITCODE%.
    pause
)

exit /b %EXITCODE%
