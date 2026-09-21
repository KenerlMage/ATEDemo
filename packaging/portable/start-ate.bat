@echo off
rem ============================================================
rem  ATE Runner - start the app (portable layout)
rem  Double-click this file, or the ATE_Launcher.exe next to it.
rem ============================================================
setlocal
cd /d "%~dp0"
title ATE Runner

set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
  echo [ERROR] runtime\python.exe not found. Package is incomplete.
  pause
  exit /b 1
)

set "ATE_WEB_DIR=%~dp0web"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "ATE_PORT=8000"

if exist "%~dp0ATE_Launcher.exe" (
  "%~dp0ATE_Launcher.exe" %*
  exit /b %errorlevel%
)

"%PY%" "%~dp0app\ate_launcher.py" %*
exit /b %errorlevel%
