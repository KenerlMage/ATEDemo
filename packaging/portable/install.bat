@echo off
rem ============================================================
rem  ATE Runner - one-click deploy of the PORTABLE zip contents
rem  Copies this folder to %SystemDrive%\ATERunner, creates
rem  Desktop + Start Menu shortcuts and an uninstall entry.
rem  No admin rights required if the target stays under the
rem  user profile or another writable folder.
rem ============================================================
setlocal
title ATE Runner - install
set "SRC=%~dp0"
set "DST=%SystemDrive%\ATERunner"
if not "%~1"=="" set "DST=%~1"

echo Source : %SRC%
echo Target : %DST%
echo.
echo [1/4] copying files ...
robocopy "%SRC%." "%DST%" /E /NFL /NDL /NJH /NJS /XF VERSION.txt >nul
if errorlevel 8 (
  echo [ERROR] copy failed. Try running this script as administrator,
  echo         or pass a writable target:  install.bat D:\ATERunner
  pause
  exit /b 1
)

echo [2/4] creating shortcuts ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d='%DST%'; $ws=New-Object -ComObject WScript.Shell; $t=Join-Path $d 'ATE_Launcher.exe'; if(-not (Test-Path $t)){$t=Join-Path $d 'start-ate.bat'}; foreach($p in @(([Environment]::GetFolderPath('Desktop')), (Join-Path ([Environment]::GetFolderPath('Programs')) 'ATE Runner'))){ if(-not (Test-Path $p)){New-Item -ItemType Directory -Force -Path $p | Out-Null}; $s=$ws.CreateShortcut((Join-Path $p 'ATE Runner.lnk')); $s.TargetPath=$t; $s.WorkingDirectory=$d; $s.Description='ATE Runner'; $s.Save() }"

echo [3/4] registering uninstaller ...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ATERunner" /v DisplayName /t REG_SZ /d "ATE Runner" /f >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ATERunner" /v DisplayVersion /t REG_SZ /d "portable" /f >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ATERunner" /v InstallLocation /t REG_SZ /d "%DST%" /f >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ATERunner" /v UninstallString /t REG_SZ /d "\"%DST%\uninstall.bat\"" /f >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ATERunner" /v NoModify /t REG_DWORD /d 1 /f >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ATERunner" /v NoRepair /t REG_DWORD /d 1 /f >nul

echo [4/4] opening ATE Runner ...
start "" "%DST%\ATE_Launcher.exe"
if errorlevel 1 start "" "%DST%\start-ate.bat"
echo.
echo Installed to %DST%
echo A shortcut "ATE Runner" was created on the Desktop and in the Start Menu.
pause
