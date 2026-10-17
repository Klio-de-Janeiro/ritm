@echo off
setlocal
cd /d "%~dp0"
for %%V in (3.13 3.12 3.14 3.11 3.10) do (
    py -%%V -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
    if not errorlevel 1 (
        set "RITM_BUILD_SELECTOR=-%%V"
        goto run_py
    )
)
python -c "import sys,struct; assert (3,10) <= sys.version_info[:2] < (3,15); assert struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 goto no_python
python "%~dp0build_windows.py" %*
exit /b %errorlevel%
:run_py
py %RITM_BUILD_SELECTOR% "%~dp0build_windows.py" %*
exit /b %errorlevel%
:no_python
echo Build requires Windows and 64-bit Python 3.10-3.14.
echo Install Python from https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH" or the Python launcher.
exit /b 1
