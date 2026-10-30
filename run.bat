@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem Find Python in this order:
rem   1. GPT_SOVITS_HOME runtime
rem   2. sibling GPT-SoVITS install
rem   3. python on PATH
set "PYTHON="
if defined GPT_SOVITS_HOME if exist "%GPT_SOVITS_HOME%\runtime\python.exe" set "PYTHON=%GPT_SOVITS_HOME%\runtime\python.exe"
if not defined PYTHON if exist "%~dp0..\GPT-SoVITS-v2pro-20250604-nvidia50\runtime\python.exe" set "PYTHON=%~dp0..\GPT-SoVITS-v2pro-20250604-nvidia50\runtime\python.exe"
if not defined PYTHON if exist "%~dp0..\GPT-SoVITS\runtime\python.exe" set "PYTHON=%~dp0..\GPT-SoVITS\runtime\python.exe"
if not defined PYTHON (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON goto :no_python

"%PYTHON%" -B -u -X "pycache_prefix=%~dp0cache\pycache" "%~dp0gptsovits_auto.py" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" if "%~1"=="" pause
exit /b %CODE%

:no_python
echo [ERROR] Python not found.
echo.
echo Set GPT_SOVITS_HOME to the GPT-SoVITS install folder, for example:
echo   set GPT_SOVITS_HOME=D:\GPT-SoVITS
echo   run.bat init --install "%%GPT_SOVITS_HOME%%"
echo.
echo Or install Python 3.9+ and run:
echo   pip install -r requirements.txt
if "%~1"=="" pause
exit /b 2
