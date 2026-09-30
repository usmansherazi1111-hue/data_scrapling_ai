@echo off
rem Starts Scrapling Studio and opens it in your browser.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Not set up yet. Run setup.bat first.
  pause
  exit /b 1
)
set PORT=
for /f "tokens=2 delims==" %%p in ('findstr /b "PORT=" .env 2^>nul') do set PORT=%%p
if "%PORT%"=="" set PORT=8000
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:%PORT%"
.venv\Scripts\python.exe main.py
pause
