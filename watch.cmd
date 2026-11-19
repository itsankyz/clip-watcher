@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo Python environment is missing. Run setup.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" clip_watcher.py %*
exit /b %ERRORLEVEL%
