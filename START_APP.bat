@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE="
set "PYTHON_ARGS="
if exist "%~dp0runtime\python.exe" set "PYTHON_EXE=%~dp0runtime\python.exe"
if not defined PYTHON_EXE where py >nul 2>&1 && set "PYTHON_EXE=py" && set "PYTHON_ARGS=-3"
if not defined PYTHON_EXE where python >nul 2>&1 && set "PYTHON_EXE=python"
if not defined PYTHON_EXE (
    echo Python 3 was not found. Install Python 3 or place it in runtime\python.exe.
    pause
    exit /b 1
)
set "APP_DEPS=%LOCALAPPDATA%\EDEngineeringCompanion\python-deps"
set "PYTHONPATH=%APP_DEPS%;%PYTHONPATH%"
"%PYTHON_EXE%" %PYTHON_ARGS% -c "import PySide6, zmq, requests" >nul 2>&1
if errorlevel 1 (
    echo Preparing the GPU and EDDN runtime for first launch...
    call INSTALL_REQUIREMENTS.bat --automatic
    if errorlevel 1 exit /b 1
)
"%PYTHON_EXE%" %PYTHON_ARGS% phase14_main.py
endlocal
