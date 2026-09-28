@echo off
setlocal EnableDelayedExpansion
rem One-time setup on Windows: virtual env, Python packages, browsers, .env
cd /d "%~dp0"
set PY=
for %%v in (3.12 3.11 3.10) do if not defined PY (py -%%v --version >nul 2>nul && set "PY=py -%%v")
if not defined PY set PY=python
%PY% --version || (echo Python 3.10-3.12 is required: https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv %PY% -m venv .venv || (pause & exit /b 1)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt || (pause & exit /b 1)
echo Installing browsers (about 500 MB)...
.venv\Scripts\scrapling.exe install || (pause & exit /b 1)
if not exist .env copy .env.example .env >nul
echo.
echo Setup complete. Start the app with run.bat
pause
