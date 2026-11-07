@echo off
rem =====================================================================
rem  NetTool packaging script (PyInstaller onefile + windowed)
rem  ASCII-only on purpose: cmd.exe parses .bat with the system OEM/ANSI
rem  codepage, so non-ASCII bytes here can corrupt commands.
rem
rem  Must use an interpreter that has PySide6 installed, otherwise the
rem  PyInstaller analysis stage cannot find PySide6.
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
    echo [ERROR] No Python interpreter with PySide6 was found; cannot package.
    echo.
    echo   Tried: python.path, .venv\Scripts\python.exe, "python" on PATH
    echo.
    echo   Create a "python.path" file next to this script containing one line:
    echo   the full path to a python.exe that already has PySide6 installed.
    echo   Or install the dependencies into the current interpreter:
    echo       python -m pip install -r requirements.txt
    echo.
    pause
    endlocal
    exit /b 1
)

echo ============================================
echo   NetTool packaging (PyInstaller onefile)
echo ============================================
echo   interpreter: %NETTOOL_PY%
echo.

rem Ensure PyInstaller is ready (install into the same interpreter).
"%NETTOOL_PY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [1/2] pyinstaller not found, installing...
    rem Aliyun mirror: the official PyPI index is ~15x slower on this machine.
    "%NETTOOL_PY%" -m pip install "pyinstaller>=6.0" -i https://mirrors.aliyun.com/pypi/simple/
    if errorlevel 1 (
        echo Failed to install pyinstaller. Check network or pip config.
        pause
        endlocal
        exit /b 1
    )
)

echo [2/2] building...
"%NETTOOL_PY%" -m PyInstaller --noconfirm --clean NetTool.spec
if errorlevel 1 (
    echo.
    echo Packaging failed. See log above.
    pause
    endlocal
    exit /b 1
)

echo.
echo Done. Output: dist\NetTool.exe
pause
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
