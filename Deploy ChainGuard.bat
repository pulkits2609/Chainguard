@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo Install Python 3.11+ first. Node.js 22.12+ and PostgreSQL are deployment prerequisites.
    if "%~1"=="" pause
    exit /b 1
)
python deploy.py %*
set "CHAINGUARD_EXIT=%ERRORLEVEL%"
if not "%CHAINGUARD_EXIT%"=="0" if "%~1"=="" pause
exit /b %CHAINGUARD_EXIT%
