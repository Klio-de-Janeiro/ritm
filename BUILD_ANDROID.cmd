@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_android.ps1"
exit /b %errorlevel%
