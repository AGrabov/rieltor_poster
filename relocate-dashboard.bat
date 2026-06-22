@echo off
rem One-time: move the install out of C:\Program Files into a user-writable folder
rem so the dashboard needs no admin / no UAC. SAFE - it copies, keeps the original.
rem Close the running dashboard first, then double-click this file once.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0relocate-dashboard.ps1"
echo.
pause
