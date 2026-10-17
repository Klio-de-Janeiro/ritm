@echo off
setlocal
cd /d "%~dp0"
for %%V in (3.13 3.12 3.14 3.11 3.10) do (
    py -%%V -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "RITM_GPT_SELECTOR=-%%V"
        goto use_py
    )
)
python -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,15)" >nul 2>&1
if errorlevel 1 goto missing_python
python "%~dp0setup_gpt.py"
exit /b %errorlevel%
:use_py
py %RITM_GPT_SELECTOR% "%~dp0setup_gpt.py"
exit /b %errorlevel%
:missing_python
echo Install Python 3.10-3.14, then run SETUP_GPT again.
exit /b 1
