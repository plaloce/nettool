@echo off
rem =====================================================================
rem  NetTool launcher  (ASCII-only on purpose: cmd.exe parses .bat with the
rem  system OEM/ANSI codepage, so non-ASCII bytes here can corrupt commands.)
rem  Uses the Python interpreter that has PySide6 installed; falls back to
rem  .venv, then to plain "python" on PATH.
rem  Edit NETTOOL_PY below to point at your own virtualenv if needed.
rem =====================================================================
setlocal
cd /d "%~dp0"

set "NETTOOL_PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%NETTOOL_PY%" (
    if exist "%~dp0.venv\Scripts\python.exe" (
        set "NETTOOL_PY=%~dp0.venv\Scripts\python.exe"
    ) else (
        set "NETTOOL_PY=python"
    )
)

echo [NetTool] using interpreter: %NETTOOL_PY%
"%NETTOOL_PY%" main.py
if errorlevel 1 (
    echo.
    echo [NetTool] exited with error code %errorlevel%
    echo If PySide6 is missing, install it first:
    echo     "%NETTOOL_PY%" -m pip install -r requirements.txt
    pause
)
endlocal
