@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" clip_watcher.py %*
) else (
  python clip_watcher.py %*
)
exit /b %ERRORLEVEL%
