@echo off
echo ============================================
echo   Store Intelligence System — Startup
echo ============================================
echo.

cd /d "%~dp0"

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

REM Install dependencies if needed
if not exist "backend\venv" (
    echo [1/3] Creating virtual environment...
    python -m venv backend\venv
)

echo [2/3] Installing dependencies...
backend\venv\Scripts\pip install -q -r backend\requirements.txt

REM Copy env if not present
if not exist "backend\.env" (
    echo [INFO] Creating .env from .env.example ...
    copy .env.example backend\.env >nul
)

echo [3/3] Starting Store Intelligence System...
echo.
echo Dashboard ^→ http://localhost:8000
echo API Docs  ^→ http://localhost:8000/docs
echo.
echo Press Ctrl+C to stop.
echo.

backend\venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload --app-dir backend
pause
