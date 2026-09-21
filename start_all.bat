@echo off
setlocal
title ATE Launcher
cd /d "%~dp0"

echo ============================================
echo   ATE Automatic Test Equipment - Launcher
echo ============================================
echo.

echo [1/4] Checking Python...
set "PY=python"
set "PYABS=python"
if exist "backend\.venv\Scripts\python.exe" (
  set "PY=backend\.venv\Scripts\python.exe"
  set "PYABS=%~dp0backend\.venv\Scripts\python.exe"
  echo [1/4] Using venv: backend\.venv
)
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found in PATH. Please install Python 3 first.
  pause
  exit /b 1
)

echo [2/4] Installing backend dependencies...
%PY% -m pip install -r backend\requirements.txt -q
if errorlevel 1 (
  echo [ERROR] Backend dependency install failed. Check network and retry.
  pause
  exit /b 1
)

if not exist frontend\node_modules (
  echo [3/4] Installing frontend dependencies...
  call npm install --prefix frontend
  if errorlevel 1 (
    echo [ERROR] Frontend dependency install failed. Check network and retry.
    pause
    exit /b 1
  )
) else (
  echo [3/4] Frontend dependencies already installed.
)

echo [4/4] Starting services...
start "ATE-Backend" cmd /k "cd /d %~dp0backend && %PYABS% -m uvicorn main:app --host 0.0.0.0 --port 8000"
start "ATE-Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo   Backend : http://127.0.0.1:8000  (API docs at /docs)
echo   Frontend: http://127.0.0.1:5173
echo   Wait for both windows to be ready, then open the frontend URL.
echo.
pause
