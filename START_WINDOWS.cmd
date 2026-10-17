@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import sys; assert sys.version_info >= (3,10)" >nul 2>&1
if not errorlevel 1 goto use_py
python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>&1
if not errorlevel 1 goto use_python
echo Python 3.10 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH" in the installer.
pause
exit /b 1
:use_py
py -3 launcher.py
goto finished
:use_python
python launcher.py
:finished
if errorlevel 1 pause
endlocal
