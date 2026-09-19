@echo off
cd /d "%~dp0"
start "qrapp-waitress" /MIN "venv\Scripts\waitress-serve.exe" --listen=0.0.0.0:8001 --threads=12 qrproject.wsgi:application
