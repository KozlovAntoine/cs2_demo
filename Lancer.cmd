@echo off
where pythonw.exe >nul 2>&1
if not errorlevel 1 (
    start "" pythonw.exe "%~dp0utility_app.py"
    exit /b
)
python "%~dp0utility_app.py"
if errorlevel 1 pause
