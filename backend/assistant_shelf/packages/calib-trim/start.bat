@echo off
chcp 65001 >nul
title 常数标定计算助手 2.0.0
echo 常数标定计算助手 2.0.0
echo.
echo 正在启动本地页面...
start "" "%~dp0index.html"
echo.
echo 已打开助手页面；关闭本窗口不影响助手运行。
timeout /t 3 >nul
exit
