@echo off
chcp 65001 >nul
title Camera Assistant
setlocal enabledelayedexpansion
set "APP=%~dp0main.py"
set "PY="
if exist "%~dp0.venv\Scripts\pythonw.exe" set "PY=%~dp0.venv\Scripts\pythonw.exe"
if not defined PY if exist "%~dp0runtime\pythonw.exe" set "PY=%~dp0runtime\pythonw.exe"
if not defined PY if defined ATE_ASSISTANT_PYTHON if exist "%ATE_ASSISTANT_PYTHON%" set "PY=%ATE_ASSISTANT_PYTHON%"
if not defined PY if exist "D:\ATE\tools\camera_assistant\.venv\Scripts\pythonw.exe" set "PY=D:\ATE\tools\camera_assistant\.venv\Scripts\pythonw.exe"
if not defined PY set "PY=pythonw"
echo Launching Camera Assistant as a standalone window...
start "Camera Assistant" "%PY%" "%APP%"
if errorlevel 1 (
  echo Failed to launch. Set ATE_ASSISTANT_PYTHON to a pythonw.exe that has PySide6 installed.
  pause
)
endlocal
exit /b 0
