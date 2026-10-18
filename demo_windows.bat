@echo off
chcp 65001 >nul
title Hushyor AI - DEMO
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [XATO] Avval start_windows.bat yoki install_windows.bat ni yurgizing ^(.venv yo'q^).
  pause
  exit /b 1
)
echo.
echo  Hushyor AI - DEMO rejimi (namuna do'kon, haqiqiy ma'lumotga tegmaydi)
echo  Panel: http://127.0.0.1:8090   login: demo   parol: demo12345
echo.
".venv\Scripts\python.exe" scripts\demo.py %*
pause
