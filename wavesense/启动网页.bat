@echo off
chcp 65001 >nul
rem WaveSense one-click launcher (Windows). Double-click to start the web app.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  where python >nul 2>nul && set "PY=python"
)

if not defined PY (
  echo Python 3 not found. Please install Python 3 from python.org and re-run.
  pause
  exit /b 1
)

set "PYTHONPATH=."
echo Starting WaveSense web app...
"%PY%" -m src.main --web
echo.
echo Window closed / server stopped.
pause
