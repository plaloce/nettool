@echo off
rem =====================================================================
rem  NetTool launcher  (ASCII-only on purpose: cmd.exe parses .bat with the
rem  system OEM/ANSI codepage, so non-ASCII bytes here can corrupt commands.)
rem
rem  Interpreter resolution order (each candidate is validated by actually
rem  importing PySide6, so an unusable one is skipped instead of failing):
rem     1) python.path   (one line, full path; local only, gitignored)
rem     2) .venv\Scripts\python.exe
rem     3) "python" on PATH
rem =====================================================================
setlocal
cd /d "%~dp0"

set "NETTOOL_PY="

if exist "%~dp0python.path" for /f "usebackq delims=" %%P in ("%~dp0python.path") do call :try_candidate "%%~P"
if not defined NETTOOL_PY if exist "%~dp0.venv\Scripts\python.exe" call :try_candidate "%~dp0.venv\Scripts\python.exe"
if not defined NETTOOL_PY call :try_candidate "python"

if not defined NETTOOL_PY (
    echo.
    echo [NetTool] No Python interpreter with PySide6 was found.
    echo.
    echo   Tried: python.path, .venv\Scripts\python.exe, "python" on PATH
    echo.
    echo   Fix it in one of these ways:
    echo     1^) Create a file named "python.path" next to this script,
    echo        containing one line: the full path to a python.exe that
    echo        already has PySide6 installed.
    echo     2^) Create a virtual environment here:
    echo            python -m venv .venv
    echo            .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo     3^) Install the dependencies into the "python" on your PATH:
    echo            python -m pip install -r requirements.txt
    echo.
    pause
    endlocal
    exit /b 1
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
exit /b 0

rem ---------------------------------------------------------------------
rem  :try_candidate <path-or-command>
rem  Accepts the first candidate that can import PySide6; later calls are
rem  no-ops once NETTOOL_PY is set.
rem ---------------------------------------------------------------------
:try_candidate
if defined NETTOOL_PY exit /b 0
"%~1" -c "import PySide6" >nul 2>nul
if errorlevel 1 exit /b 0
set "NETTOOL_PY=%~1"
exit /b 0
