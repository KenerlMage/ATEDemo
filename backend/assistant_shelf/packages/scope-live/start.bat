@echo off
chcp 65001 >nul
title 示波器实时观测助手 1.0.3
echo 示波器实时观测助手 1.0.3
echo.
echo 正在启动本地页面...
start "" "%~dp0index.html"
echo.
echo 已打开助手页面；关闭本窗口不影响助手运行。
timeout /t 3 >nul
exit
