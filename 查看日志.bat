@echo off
cd /d "%~dp0"
if exist service.log (
    type service.log
) else (
    echo No log file found.
)
pause
