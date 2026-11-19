@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if not errorlevel 1 (
    uv venv --python 3.12 --seed .venv
    if errorlevel 1 goto fail
    .venv\Scripts\python.exe -m pip install -r requirements-audio.txt
    if errorlevel 1 goto fail
    goto done
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements-audio.txt
if errorlevel 1 goto fail
:done
echo Ready. Double-click start.bat to open Telegram Chat Parser.
pause
exit /b 0
:fail
echo Setup failed. Install Python 3.12 with Tcl/Tk and pip, or install uv.
echo See README.md for details.
pause
exit /b 1
