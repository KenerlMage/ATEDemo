@echo off
setlocal
cd /d "%~dp0"

echo [ATE] Checking Python...
set "PY=python"
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
  echo [ATE] Using venv: .venv
)
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found. Please install Python 3 first.
  pause
  exit /b 1
)

echo [ATE] Installing backend dependencies...
%PY% -m pip install -r requirements.txt -q
if errorlevel 1 (
  echo [ERROR] Backend dependency install failed. Check network and retry.
  pause
  exit /b 1
)

echo [ATE] Starting backend: http://127.0.0.1:8000  (API docs at /docs)
%PY% -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
