@echo off
setlocal
title ATE Launcher
chcp 65001 >nul
cd /d "%~dp0"

set "BACKEND_PORT=8000"
set "UI_URL=http://127.0.0.1:%BACKEND_PORT%/"

echo ============================================
echo   ATE Automatic Test Equipment - Launcher
echo ============================================
echo.

rem ---------- 1/5 python ----------
echo [1/5] Checking Python...
set "PY=python"
set "PYABS=python"
if defined ATE_PYTHON if exist "%ATE_PYTHON%" (
  set "PY=%ATE_PYTHON%"
  set "PYABS=%ATE_PYTHON%"
  echo [1/5] Using ATE_PYTHON
)
if not defined ATE_PYTHON if exist "backend\.venv\Scripts\python.exe" (
  set "PY=backend\.venv\Scripts\python.exe"
  set "PYABS=%~dp0backend\.venv\Scripts\python.exe"
  echo [1/5] Using venv: backend\.venv
)
if not defined ATE_PYTHON if not exist "backend\.venv\Scripts\python.exe" (
  python --version >nul 2>&1
  if not errorlevel 1 (
    echo [1/5] Creating backend\.venv for a clean, portable install...
    python -m venv backend\.venv >nul 2>&1
    if exist "backend\.venv\Scripts\python.exe" (
      set "PY=backend\.venv\Scripts\python.exe"
      set "PYABS=%~dp0backend\.venv\Scripts\python.exe"
      echo [1/5] Using venv: backend\.venv ^(created^)
    )
  )
)
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.10+ not found. Install it and add to PATH, or set ATE_PYTHON.
  pause
  exit /b 1
)

rem ---------- 2/5 backend deps (install only when missing) ----------
echo [2/5] Checking backend dependencies...
%PY% -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo [2/5] Installing backend dependencies...
  if exist wheels (
    %PY% -m pip install --no-index --find-links wheels -r backend\requirements.txt -q
  ) else (
    %PY% -m pip install -r backend\requirements.txt -q
  )
  %PY% -c "import fastapi, uvicorn" >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Backend dependency install failed. Check network and retry.
    pause
    exit /b 1
  )
)

rem ---------- 3/5 frontend build output ----------
rem frontend/dist is committed: target PC does not need Node.js;
rem only try npm when dist is missing (requires Node.js).
if exist frontend\dist\index.html (
  echo [3/5] frontend/dist found - skip Node/npm.
  goto :frontend_ready
)
echo [3/5] frontend/dist missing - Node.js is required to build it.
where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js/npm not found and frontend\dist is missing.
  echo         Either copy a built frontend\dist here, or install Node.js 18+ and retry.
  pause
  exit /b 1
)
if not exist frontend\node_modules (
  echo [3/5] Installing frontend dependencies...
  call npm install --prefix frontend
  if errorlevel 1 (
    echo [ERROR] Frontend dependency install failed. Check network and retry.
    pause
    exit /b 1
  )
)
echo [3/5] Building frontend...
call npm run build --prefix frontend
if errorlevel 1 (
  echo [ERROR] Frontend build failed.
  pause
  exit /b 1
)
:frontend_ready

rem ---------- 4/5 port check ----------
netstat -ano | findstr ":%BACKEND_PORT% " | findstr LISTENING >nul 2>&1
if not errorlevel 1 (
  echo [ERROR] Port %BACKEND_PORT% is already in use. Close it first:
  echo         netstat -ano ^| findstr ":%BACKEND_PORT% "
  pause
  exit /b 1
)
echo [4/5] port %BACKEND_PORT% is free

rem ---------- 5/5 start services and open Edge ----------
echo [5/5] Starting services...
start "ATE-Backend" cmd /k "cd /d %~dp0backend && %PYABS% -m uvicorn main:app --host 0.0.0.0 --port %BACKEND_PORT%"

rem Backend serves frontend/dist directly, so only the backend window is needed.
rem set ATE_NO_BROWSER=1 to skip opening the browser (automation)
if not "%ATE_NO_BROWSER%"=="1" (
  start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tools\open_ui.ps1" -Url "%UI_URL%"
  echo.
  echo   Browser will open automatically when the backend is ready: %UI_URL%
) else (
  echo.
  echo   ATE_NO_BROWSER=1 set - skip opening the browser.
)

echo.
echo   Backend : http://127.0.0.1:%BACKEND_PORT%  - API docs at /docs
echo   Frontend: %UI_URL%
echo.
pause
