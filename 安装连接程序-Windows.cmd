@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo 请先从 python.org 安装 Python 3.12，再运行本文件。
  pause
  exit /b 1
)
py -3.12 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-desktop.txt
if errorlevel 1 (
  echo 依赖安装未完成，请检查网络后重试。
  pause
  exit /b 1
)
echo 准备完成。请安装苹果设备支持驱动，并运行“一键连接-Windows.cmd”。
pause
