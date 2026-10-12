@echo off
rem climaterisk — double-click to start. Runs start.ps1 next to this file (no installation).
rem Keep this folder in a SHORT path, e.g. C:\climaterisk (Windows 260-character path limit).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause
