@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -u "%~dp0notebook.py" %*
) else (
    set "PYTHONPATH=%~dp0..\..\work\python-deps"
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -u "%~dp0notebook.py" %*
)
if errorlevel 1 pause
endlocal
