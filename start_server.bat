@echo off
REM ============================================================
REM  EVENT-DAY SERVER (production)
REM  1) migrations + static files   2) Waitress on port 8001
REM  3) Cloudflare Tunnel (prints the https URL for scanner phones)
REM
REM  Prerequisite: MySQL running (XAMPP Control Panel -> Start MySQL)
REM ============================================================
cd /d "%~dp0"

set PYTHON=%~dp0venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

REM Concurrent requests in flight (NOT the number of users/scanners).
REM 16 threads = up to 16 DB connections; MariaDB default max is 151.
set WAITRESS_THREADS=16
set WAITRESS_PORT=8001

echo [1/3] Applying migrations and collecting static files...
"%PYTHON%" manage.py migrate --noinput
"%PYTHON%" manage.py collectstatic --noinput

echo [2/3] Starting Waitress on http://0.0.0.0:%WAITRESS_PORT% ...
start "qrapp-waitress" /MIN "%PYTHON%" qrproject\wsgi.py

timeout /t 3 /nobreak >nul

echo [3/3] Starting Cloudflare Tunnel (keep this window OPEN)...
echo   The https://....trycloudflare.com URL printed below is what
echo   scanner phones should open. It changes on every run.
echo.
cloudflared tunnel --url http://localhost:%WAITRESS_PORT%

pause
