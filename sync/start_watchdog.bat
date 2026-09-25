@echo off
rem sync/start_watchdog.bat - run the watchdog under the REAL python.exe.
rem
rem On Windows a bare "python" can resolve to a launcher/shim (the Microsoft
rem Store alias or the Python install manager) that spawns the real
rem interpreter as a child and sits there as a separate PID. Stopping that
rem PID does not stop the watchdog (observed 2026-09-15). This asks the
rem interpreter for its own path once, checks it has the bot's packages,
rem then runs the watchdog directly under it - so the PID you see IS the
rem watchdog, and "stop" means stop.
setlocal
for /f "usebackq delims=" %%i in (`python -c "import sys; print(sys.executable)"`) do set "PY=%%i"
if not defined PY (
    echo Could not resolve python.exe - is Python on PATH?
    exit /b 1
)
"%PY%" -c "import MetaTrader5, pandas, requests, dotenv" >nul 2>&1
if errorlevel 1 (
    echo %PY% is missing MetaTrader5/pandas/requests/python-dotenv - install requirements.txt into THIS interpreter.
    exit /b 1
)
cd /d "%~dp0.."
echo Launching watchdog under %PY%
"%PY%" sync\watchdog.py
