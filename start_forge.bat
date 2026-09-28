@echo off
rem Start DK64 Forge. Creates the virtualenv and installs the requirements on first run.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First run: creating .venv and installing requirements ...
    where py >nul 2>nul
    if errorlevel 1 (
        python -m venv .venv
    ) else (
        py -3 -m venv .venv
    )
    if errorlevel 1 (
        echo Could not create the virtualenv. Install Python 3.12 or newer from python.org first.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Installing the requirements failed.
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -m dk64_forge
if errorlevel 1 pause
