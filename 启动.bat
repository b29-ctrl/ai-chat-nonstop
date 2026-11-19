@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [x] 未找到 python，请先安装 Python 3.10+ 并加入 PATH。
  pause
  exit /b 1
)

start "AI聊不停" python "%~dp0启动.py"
exit /b 0
