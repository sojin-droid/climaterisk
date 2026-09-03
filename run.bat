@echo off
rem climaterisk launcher (Windows) - double-click to start backend + frontend.
rem Delegates to run.ps1 (the Windows equivalent of run.command).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
pause
