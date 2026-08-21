@echo off
setlocal enabledelayedexpansion

echo.
echo  ============================================
echo   lazyctl - Windows Setup
echo  ============================================
echo.

:: ── 1. Check Python ───────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found on PATH.
    echo  Please install Python 3.10+ from https://www.python.org/downloads/
    echo  Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK] Python %PYVER% detected.

:: ── 2. Install dependencies ────────────────────────────────────────────────
echo.
echo  Installing required packages (prompt_toolkit, rich)...
python -m pip install --upgrade pip --quiet
python -m pip install -r "%~dp0requirements.txt" --quiet
if errorlevel 1 (
    echo  [ERROR] pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo  [OK] Dependencies installed.

:: ── 3. Create a launcher script in %LOCALAPPDATA%\lazyctl\ ─────────────────
set LAZYCTL_DIR=%LOCALAPPDATA%\lazyctl
if not exist "%LAZYCTL_DIR%" mkdir "%LAZYCTL_DIR%"

set SCRIPT_PATH=%~dp0lazyctl-v4.py
set LAUNCHER=%LAZYCTL_DIR%\lazyctl.bat

echo @echo off > "%LAUNCHER%"
echo python "%SCRIPT_PATH%" %%* >> "%LAUNCHER%"

echo  [OK] Launcher created at: %LAUNCHER%

:: ── 4. Offer to add to User PATH ──────────────────────────────────────────
echo.
set /p ADD_PATH=" Add '%LAZYCTL_DIR%' to your user PATH? [Y/N]: "
if /i "!ADD_PATH!"=="Y" (
    for /f "usebackq tokens=2,*" %%a in (`reg query "HKCU\Environment" /v PATH 2^>nul`) do set CURRENT_PATH=%%b
    echo !CURRENT_PATH! | findstr /i "%LAZYCTL_DIR%" >nul 2>&1
    if errorlevel 1 (
        setx PATH "!CURRENT_PATH!;%LAZYCTL_DIR%" >nul
        echo  [OK] PATH updated. Open a new terminal and run: lazyctl
    ) else (
        echo  [OK] Already in PATH.
    )
) else (
    echo  [SKIP] You can run lazyctl manually with:
    echo         python "%SCRIPT_PATH%"
)

echo.
echo  ============================================
echo   Setup complete!  Run: lazyctl
echo  ============================================
echo.
pause
