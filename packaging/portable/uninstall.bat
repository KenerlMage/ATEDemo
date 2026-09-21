@echo off
rem ============================================================
rem  ATE Runner - uninstaller (portable / installer edition)
rem  Removes shortcuts and the uninstall registry entry.
rem  Keeps test data by default (logs / workspace / ate.db).
rem ============================================================
setlocal
title ATE Runner - uninstall
set "ROOT=%~dp0"
if "%~1"=="" (
  echo This will uninstall ATE Runner from:
  echo   %ROOT%
  echo.
  set /p ANS=Remove the application folder as well [y/N]:
  set "KEEP=1"
  if /i "%ANS%"=="y" set "KEEP="
) else (
  set "KEEP="
)

echo [1/3] removing shortcuts ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$names=@('ATE Runner.lnk'); $dirs=@(([Environment]::GetFolderPath('Desktop')), (Join-Path ([Environment]::GetFolderPath('Programs')) 'ATE Runner')); foreach($d in $dirs){ foreach($n in $names){ $p=Join-Path $d $n; if(Test-Path $p){ Remove-Item $p -Force -ErrorAction SilentlyContinue } } }"

echo [2/3] removing registry entry ...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ATERunner" /f >nul 2>&1

echo [3/3] removing files ...
cd /d "%TEMP%"
if defined KEEP (
  echo   kept: logs\  workspace\  app\ate.db
  for %%D in (app web runtime) do if exist "%ROOT%%%D" rd /s /q "%ROOT%%%D"
  for %%F in (ATE_Launcher.exe start-ate.bat install.bat uninstall.bat VERSION.txt README.txt) do if exist "%ROOT%%%F" del /q "%ROOT%%%F"
) else (
  rd /s /q "%ROOT%"
)
echo.
echo ATE Runner uninstalled.
pause
