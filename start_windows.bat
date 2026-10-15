@echo off
chcp 65001 >nul
title Hushyor AI
cd /d "%~dp0"

echo.
echo  ==========================================
echo    Hushyor AI - do'kon kameralari uchun AI
echo  ==========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  set PY=py -3
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [XATO] Python topilmadi.
    echo https://www.python.org/downloads/ dan Python 3.11 yoki 3.12 ni o'rnating
    echo va o'rnatishda "Add python.exe to PATH" belgisini qo'ying.
    pause
    exit /b 1
  )
  set PY=python
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Virtual muhit yaratilmoqda...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [XATO] Virtual muhit yaratilmadi.
    pause
    exit /b 1
  )
)

rem requirements.txt o'zgarsa (yangi versiya) kutubxonalar o'zi yangilanadi: belgi faylida uning xeshi
set "REQHASH="
for /f "tokens=* delims=" %%h in ('certutil -hashfile requirements.txt SHA256 ^| findstr /v ":"') do set "REQHASH=%%h"
set "REQHASH=%REQHASH: =%"
set "OLDHASH="
if exist ".venv\.installed" set /p OLDHASH=<".venv\.installed"
if not "%REQHASH%"=="%OLDHASH%" (
  echo [2/3] Kutubxonalar o'rnatilmoqda yoki yangilanmoqda - 3-10 daqiqa...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [XATO] Kutubxonalarni o'rnatib bo'lmadi. Internetni tekshirib, qayta urinib ko'ring.
    pause
    exit /b 1
  )
  > ".venv\.installed" echo %REQHASH%
)

echo [3/3] Dastur ishga tushmoqda... Brauzer o'zi ochiladi: http://localhost:8000
".venv\Scripts\python.exe" run.py
pause
