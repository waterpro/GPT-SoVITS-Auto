@echo off
call "%~dp0run.bat" voices add %*
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%
