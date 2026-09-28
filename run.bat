@echo off
rem Starts Scrapling Studio and opens it in your browser.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Not set up yet. Run setup.bat first.
  pause
  exit /b 1
)
start "" http://127.0.0.1:8000
.venv\Scripts\python.exe main.py
