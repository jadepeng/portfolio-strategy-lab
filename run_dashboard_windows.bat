@echo off
setlocal
cd /d "%~dp0"
python scripts\serve_dashboard.py
if errorlevel 1 pause
