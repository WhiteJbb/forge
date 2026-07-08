@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "CONFIG_FILE=%~dp0config.yaml"
set "ENV_FILE=%~dp0.env"
set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "DEBUG=false"

if not exist "%CONFIG_FILE%" (
    echo ERROR: config.yaml was not found in %~dp0
    pause
    exit /b 1
)

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

for /f "usebackq delims=" %%A in (`%PYTHON_CMD% -c "import sysconfig; print(sysconfig.get_path('scripts'))"`) do set "PYTHON_SCRIPTS=%%A"

set "LITELLM_CMD=%PYTHON_SCRIPTS%\litellm.exe"
if not exist "%LITELLM_CMD%" (
    where litellm >nul 2>&1
    if errorlevel 1 (
        echo LiteLLM was not found. Installing LiteLLM proxy dependencies...
        %PYTHON_CMD% -m pip install "litellm[proxy]" --quiet
        if errorlevel 1 (
            echo ERROR: Failed to install LiteLLM. Check your Python/pip installation and network connection.
            pause
            exit /b 1
        )

        for /f "usebackq delims=" %%A in (`%PYTHON_CMD% -c "import sysconfig; print(sysconfig.get_path('scripts'))"`) do set "PYTHON_SCRIPTS=%%A"
        set "LITELLM_CMD=%PYTHON_SCRIPTS%\litellm.exe"
        if not exist "%LITELLM_CMD%" (
            echo ERROR: litellm.exe was not found after installation.
            pause
            exit /b 1
        )
    ) else (
        set "LITELLM_CMD=litellm"
    )
)

echo Starting LiteLLM proxy...
"%LITELLM_CMD%" --config "%CONFIG_FILE%"
set "EXIT_CODE=%ERRORLEVEL%"

echo LiteLLM exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
