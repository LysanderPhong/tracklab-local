@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  call 安装连接程序-Windows.cmd
  if errorlevel 1 exit /b 1
)
if not exist .venv\Scripts\python.exe exit /b 1
.venv\Scripts\python.exe -c "import pymobiledevice3" >nul 2>&1
if errorlevel 1 (
  echo 依赖未准备完成，请重新运行安装连接程序-Windows.cmd。
  pause
  exit /b 1
)
.venv\Scripts\python.exe desktop_launcher.py
if errorlevel 1 pause
