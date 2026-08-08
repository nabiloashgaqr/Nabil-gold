@echo off
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File deploy\vps_setup.ps1
pause
