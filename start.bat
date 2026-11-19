@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "parser.py"
    exit /b
)
echo First run: double-click setup.bat to install Python dependencies.
echo See README.md for manual installation.
pause
