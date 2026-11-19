@echo off
chcp 65001 >nul
cd /d "%~dp0"
python "%~dp0备份.py" --quiet
exit /b %errorlevel%
