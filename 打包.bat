@echo off
rem =====================================================================
rem  NetTool packaging script (PyInstaller onefile + windowed)
rem  ASCII-only on purpose: cmd.exe parses .bat with the system OEM/ANSI
rem  codepage, so non-ASCII bytes here can corrupt commands.
rem  Must use the interpreter that has PySide6 installed, otherwise the
rem  PyInstaller analysis stage fails to find PySide6.
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

echo ============================================
echo   NetTool packaging (PyInstaller onefile)
echo ============================================
echo   interpreter: %NETTOOL_PY%
echo.

rem Verify PySide6 exists in this interpreter (packaging prerequisite).
"%NETTOOL_PY%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] PySide6 not found in this interpreter; cannot package.
    echo         Install it first:
    echo             "%NETTOOL_PY%" -m pip install -r requirements.txt
    pause
    exit /b 1
)

rem Ensure PyInstaller is ready (install into the same interpreter).
"%NETTOOL_PY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [1/2] pyinstaller not found, installing...
    rem Aliyun mirror: the official PyPI index is ~15x slower on this machine.
    "%NETTOOL_PY%" -m pip install "pyinstaller>=6.0" -i https://mirrors.aliyun.com/pypi/simple/
    if errorlevel 1 (
        echo Failed to install pyinstaller. Check network or pip config.
        pause
        exit /b 1
    )
)

echo [2/2] building...
"%NETTOOL_PY%" -m PyInstaller --noconfirm --clean NetTool.spec
if errorlevel 1 (
    echo.
    echo Packaging failed. See log above.
    pause
    exit /b 1
)

echo.
echo Done. Output: dist\NetTool.exe
pause
endlocal
