@echo off
REM ClipForge helper - Windows launcher
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ClipForge] Python was not found on PATH.
  echo Install Python 3.9 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)

if not exist ".venv\" (
  echo [ClipForge] First run - creating a virtual environment...
  python -m venv .venv || (echo Failed to create venv & pause & exit /b 1)
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt || (echo Failed to install dependencies & pause & exit /b 1)
) else (
  call .venv\Scripts\activate.bat
)

echo [ClipForge] Starting helper...
python -m clipforge.server %*
pause
