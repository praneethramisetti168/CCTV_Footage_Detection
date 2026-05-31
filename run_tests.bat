@echo off
echo ============================================
echo   Running Tests — Store Intelligence System
echo ============================================
cd /d "%~dp0"
backend\venv\Scripts\python.exe -m pytest tests/ -v --tb=short
pause
