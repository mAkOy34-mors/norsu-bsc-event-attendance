@echo off
REM ============================================================
REM  Start the dev server with HTTPS so phone cameras work.
REM  Serves on all interfaces at https://<this-pc-ip>:8001/
REM
REM  The first time you open it on a phone, the browser shows a
REM  certificate warning (self-signed cert) - tap "Advanced" then
REM  "Proceed anyway". This is expected and safe on your own LAN.
REM ============================================================
cd /d "%~dp0"

REM Use the project venv's Python explicitly (system Python may differ)
set PYTHON=%~dp0venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

echo Starting HTTPS server on https://0.0.0.0:8001/ ...
echo.
echo   On this PC : https://localhost:8001/qrapp/scanner/
echo   On phones  : https://192.168.137.101:8001/qrapp/scanner/
echo                (or https://192.168.180.147:8001/ on the Wi-Fi network)
echo.
echo   First visit on a phone: accept the certificate warning
echo   (Advanced ^> Proceed anyway), then the camera prompt.
echo.
echo Press CTRL+C to stop.
echo.

"%PYTHON%" manage.py runserver_plus --cert-file cert\dev.crt --key-file cert\dev.key 0.0.0.0:8001

pause
