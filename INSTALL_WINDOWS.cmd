@echo off
setlocal
cd /d "%~dp0"
if not exist "dist\Ritm.exe" (
  echo Build the application first: .\BUILD_WINDOWS
  pause
  exit /b 1
)
start "" /wait "%~dp0dist\Ritm.exe" --install-shortcuts
exit /b %errorlevel%
