@echo off
cd /d "%~dp0"
echo Starting Ecom Image Generator in background...

REM Stop old service if running
taskkill /f /im pythonw.exe >nul 2>&1
timeout /t 1 /nobreak >nul

REM Start service in background, output to log file
start "" /B pythonw app.py 1>service.log 2>&1

echo Started! Check service.log for details.
echo.
pause
