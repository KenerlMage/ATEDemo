@echo off
chcp 65001 >nul
cd /d %~dp0
if exist .venv\Scripts\python.exe (
  set PY=.venv\Scripts\python.exe
) else (
  set PY=python
)
%PY% -m pip install -r requirements.txt
%PY% -m uvicorn app:app --host 0.0.0.0 --port 8070
