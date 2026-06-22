@echo off
rem One-time setup: creates a "Rieltor Dashboard" shortcut on the Desktop and
rem launches the dashboard. Double-click this file once. After that, use the
rem Desktop icon to start the dashboard.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-dashboard.ps1"
echo.
pause
