@echo off
setlocal EnableExtensions EnableDelayedExpansion
title CentrAlign Worker Launcher

rem Double-click this file (or run run.cmd) to launch the project.
set "PROJ=%~dp0"
cd /d "%PROJ%"

if not exist "%PROJ%cli.py" (
  echo [ERROR] Could not find cli.py next to run.cmd.
  pause
  exit /b 1
)

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo [ERROR] Python 3.11+ was not found on PATH.
  pause
  exit /b 1
)

set "VENV=%PROJ%.venv"
set "VPY=%VENV%\Scripts\python.exe"
if not exist "%VPY%" (
  echo [setup] Creating virtual environment...
  %PY% -m venv "%VENV%"
  if errorlevel 1 (
    echo [ERROR] Failed to create the virtual environment.
    pause
    exit /b 1
  )
)

"%VPY%" -c "import fastapi,uvicorn,jinja2,playwright,ollama,pydantic" >nul 2>nul
if errorlevel 1 (
  echo [setup] Installing Python dependencies...
  "%VPY%" -m pip install --upgrade pip
  "%VPY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
  )
)

if not exist "%VENV%\.playwright.chromium" (
  echo [setup] Installing Chromium for Playwright...
  "%VPY%" -m playwright install chromium
  if errorlevel 1 (
    echo [ERROR] Could not install Chromium for Playwright.
    pause
    exit /b 1
  )
  type nul > "%VENV%\.playwright.chromium"
)

where ollama >nul 2>nul
if errorlevel 1 (
  echo [warn] Ollama was not found on PATH. Install it and start the Ollama app before running a task.
) else (
  echo [ok] Ollama detected.
)

:menu
echo.
echo  ============================================================
echo    CentrAlign Worker - autonomous browser agent
echo  ============================================================
echo    1. Launch mock apps + Web UI
echo    2. Run a CLI demo task
echo    3. Run eval scenarios A-G
echo    4. Run unit tests
echo    5. Exit
echo.
choice /c 12345 /n /m "  Select an option [1-5]: "
if errorlevel 5 exit /b 0
if errorlevel 4 goto tests
if errorlevel 3 goto eval
if errorlevel 2 goto cli
goto ui

:ensure_apps
"%VPY%" -c "import urllib.request as u;u.urlopen('http://127.0.0.1:3001/login',timeout=2);u.urlopen('http://127.0.0.1:3002/login',timeout=2)" >nul 2>nul
if not errorlevel 1 (
  echo [ok] Mock apps already running.
  goto :eof
)
echo [run] Starting mock apps (portal :3001, finance :3002)...
start "CentrAlign Mock Apps" "%VPY%" -m mockapps.run_apps
set /a _tries=0
:waitapps
"%VPY%" -c "import urllib.request as u;u.urlopen('http://127.0.0.1:3001/login',timeout=2);u.urlopen('http://127.0.0.1:3002/login',timeout=2)" >nul 2>nul
if not errorlevel 1 goto :eof
set /a _tries+=1
if !_tries! GEQ 40 (
  echo [warn] Timed out waiting for the mock apps. Continuing anyway.
  goto :eof
)
ping -n 2 127.0.0.1 >nul
goto waitapps

:ui
call :ensure_apps
echo [run] Starting Web UI on http://127.0.0.1:8000...
start "CentrAlign Worker UI" "%VPY%" -m uvicorn server.main:app --host 127.0.0.1 --port 8000
set /a _tries=0
:waitui
"%VPY%" -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8000/',timeout=2)" >nul 2>nul
if not errorlevel 1 goto readyui
set /a _tries+=1
if !_tries! GEQ 40 goto readyui
ping -n 2 127.0.0.1 >nul
goto waitui
:readyui
start "" "http://127.0.0.1:8000"
echo [ok] Web UI opened in your browser. Leave the server windows running.
goto menu

:cli
call :ensure_apps
set "TASK="
set /p "TASK=  Task [Enter for demo]: "
if "%TASK%"=="" set "TASK=Find the latest invoice from Acme Corp and enter it into the finance system."
"%VPY%" cli.py "%TASK%"
pause
goto menu

:eval
call :ensure_apps
"%VPY%" eval\scenarios.py
pause
goto menu

:tests
"%VPY%" -m pytest -q
pause
goto menu
