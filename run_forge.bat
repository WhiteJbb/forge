@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "ENV_FILE=%~dp0.env"
set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

REM Load KEY=VALUE pairs from .env.
if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if not "%%A"=="" set "%%A=%%B"
    )
)

set "PYTHON_CMD=python"
python --version >nul 2>&1
if errorlevel 1 (
    py -3 --version >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python was not found. Install Python 3 and try again.
        pause
        exit /b 1
    )
    set "PYTHON_CMD=py -3"
)

echo Installing Forge dependencies...
%PYTHON_CMD% -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Forge - Intelligent AI Gateway
echo   Starting on http://localhost:4000
echo ============================================
echo.

%PYTHON_CMD% -m src.server

set "EXIT_CODE=%ERRORLEVEL%"
echo Forge exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%