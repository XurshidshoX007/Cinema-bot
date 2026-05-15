@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "PYTHON_CMD="
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" --version >nul 2>&1 && set "PYTHON_CMD=""%VENV_PYTHON%"""
)

if not defined PYTHON_CMD (
    py -3.14 --version >nul 2>&1 && set "PYTHON_CMD=py -3.14"
)

if not defined PYTHON_CMD (
    py -3 --version >nul 2>&1 && set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    python --version >nul 2>&1 && set "PYTHON_CMD=python"
)

echo Bot va web app to'xtatilmoqda...

if defined PYTHON_CMD (
    for /f "usebackq delims=" %%P in (`%PYTHON_CMD% runtime_manager.py stop 2^>nul`) do (
        if not "%%P"=="" echo To'xtatildi: %%P
    )
) else (
    echo Python topilmadi, faqat PID fayllar bo'yicha tozalash qilinadi.
)

if exist ".stats_tunnel.pid" del /f /q ".stats_tunnel.pid" >nul 2>&1
del /f /q ".stats_webapp_url" >nul 2>&1
del /f /q ".bot-instance.pid" >nul 2>&1
del /f /q ".webapp.pid" >nul 2>&1

echo.
echo Tugadi.
exit /b 0
