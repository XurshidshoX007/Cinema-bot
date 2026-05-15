@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "PYTHON_CMD="
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "STATS_WEBAPP_URL="
set "WEBAPP_PID="
set "BOT_PID="

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

if not defined PYTHON_CMD (
    echo Ishlaydigan Python topilmadi. .venv yoki system Python kerak.
    exit /b 1
)

echo Bot va web app ishga tushirilmoqda...
echo Python: %PYTHON_CMD%

echo Eski processlar tozalanmoqda...
%PYTHON_CMD% runtime_manager.py stop >nul 2>&1
del /f /q ".webapp.pid" >nul 2>&1
del /f /q ".bot-instance.pid" >nul 2>&1
del /f /q ".stats_webapp_url" >nul 2>&1
>nul ping 127.0.0.1 -n 4

echo.
for /f "usebackq delims=" %%P in (`%PYTHON_CMD% launcher.py webapp.py webapp.log`) do set "WEBAPP_PID=%%P"
if defined WEBAPP_PID (
    > ".webapp.pid" echo !WEBAPP_PID!
    echo Web app PID: !WEBAPP_PID!
) else (
    echo Web appni ishga tushirishda xato.
    exit /b 1
)

echo.
for /f "usebackq delims=" %%U in (`%PYTHON_CMD% stats_tunnel.py start`) do set "STATS_WEBAPP_URL=%%U"
if defined STATS_WEBAPP_URL (
    > ".stats_webapp_url" echo !STATS_WEBAPP_URL!
    echo Stats mini app URL: !STATS_WEBAPP_URL!
) else (
    echo Stats mini app URL olinmadi.
)

echo.
for /f "usebackq delims=" %%P in (`%PYTHON_CMD% launcher.py main.py bot.log`) do set "BOT_PID=%%P"
if defined BOT_PID (
    >nul ping 127.0.0.1 -n 8
    if exist ".bot-instance.pid" (
        set /p BOT_LOCK_PID=<".bot-instance.pid"
        if defined BOT_LOCK_PID (
            echo Bot PID: !BOT_LOCK_PID!
        ) else (
            echo Bot PID: !BOT_PID!
        )
    ) else (
        echo Bot lock fayli yaratilmagan, start muvaffaqiyatsiz bo'ldi.
        %PYTHON_CMD% runtime_manager.py stop >nul 2>&1
        exit /b 1
    )
) else (
    echo Botni ishga tushirishda xato.
    %PYTHON_CMD% runtime_manager.py stop >nul 2>&1
    exit /b 1
)

echo.
echo Bot va web app ishga tushirildi.
exit /b 0
