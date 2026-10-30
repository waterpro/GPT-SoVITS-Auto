@echo off
setlocal
cd /d "%~dp0"

set "PYTHONW="
if defined GPT_SOVITS_HOME if exist "%GPT_SOVITS_HOME%\runtime\pythonw.exe" set "PYTHONW=%GPT_SOVITS_HOME%\runtime\pythonw.exe"
if not defined PYTHONW if exist "..\GPT-SoVITS-v2pro-20250604-nvidia50\runtime\pythonw.exe" set "PYTHONW=..\GPT-SoVITS-v2pro-20250604-nvidia50\runtime\pythonw.exe"
if not defined PYTHONW if exist "..\GPT-SoVITS\runtime\pythonw.exe" set "PYTHONW=..\GPT-SoVITS\runtime\pythonw.exe"
if not defined PYTHONW (
    where pythonw >nul 2>nul
    if not errorlevel 1 set "PYTHONW=pythonw"
)

if not defined PYTHONW (
    echo.
    echo [ERROR] Python runtime not found.
    echo Set GPT_SOVITS_HOME to your GPT-SoVITS folder, for example:
    echo     set GPT_SOVITS_HOME=D:\GPT-SoVITS
    echo Then run this file again.
    echo.
    pause
    exit /b 1
)

start "" "%PYTHONW%" -B -X "pycache_prefix=%~dp0cache\pycache" "%~dp0ui_main.py"
exit /b 0
