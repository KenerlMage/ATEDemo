@echo off
chcp 65001 >nul
title Camera Assistant
setlocal enabledelayedexpansion

rem ---------------------------------------------------------------------------
rem 1) Self-contained runtime: works without Python or any third-party library.
rem ---------------------------------------------------------------------------
set "HERE=%~dp0"
if exist "%HERE%runtime\CameraAssistant.exe" (
  start "Camera Assistant" "%HERE%runtime\CameraAssistant.exe"
  exit /b 0
)

rem ---------------------------------------------------------------------------
rem 2) Source checkout fallback: pick a pythonw that already has PySide6.
rem ---------------------------------------------------------------------------
set "APP=%HERE%main.py"
set "PY="
if exist "%HERE%.venv\Scripts\pythonw.exe" set "PY=%HERE%.venv\Scripts\pythonw.exe"
if not defined PY if defined ATE_ASSISTANT_PYTHON if exist "%ATE_ASSISTANT_PYTHON%" set "PY=%ATE_ASSISTANT_PYTHON%"
if not defined PY if exist "D:\ATE\tools\camera_assistant\.venv\Scripts\pythonw.exe" set "PY=D:\ATE\tools\camera_assistant\.venv\Scripts\pythonw.exe"
if not defined PY set "PY=pythonw"
echo No runtime folder found, launching from source...
start "Camera Assistant" "%PY%" "%APP%"
if errorlevel 1 (
  echo Failed to launch.
  echo Restore runtime\CameraAssistant.exe, or set ATE_ASSISTANT_PYTHON to a
  echo pythonw.exe that has PySide6 installed.
  pause
)
endlocal
exit /b 0
