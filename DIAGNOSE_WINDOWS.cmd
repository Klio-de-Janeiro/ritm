@echo off
setlocal
cd /d "%~dp0"
echo === Python ===
py -3 --version
echo.
echo === Runtime log ===
if exist "%LOCALAPPDATA%\Ritm\ritm.log" type "%LOCALAPPDATA%\Ritm\ritm.log"
echo.
echo === Launcher log ===
if exist "%LOCALAPPDATA%\Ritm\launcher.log" type "%LOCALAPPDATA%\Ritm\launcher.log"
echo.
echo Keep api.key and runtime.json private.
pause
endlocal
