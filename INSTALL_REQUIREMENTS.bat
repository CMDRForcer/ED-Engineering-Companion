@echo off
setlocal
title ED Engineering Companion - Dependencies
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
echo Installing required Python packages...
"%PYTHON_EXE%" %PYTHON_ARGS% -m pip install -r requirements.txt --target "%APP_DEPS%" --upgrade
if errorlevel 1 (
    echo.
    echo Installation failed. Verify that Python is available in PATH.
    pause
    exit /b 1
)
echo.
echo Dependencies installed successfully.
if /i not "%~1"=="--automatic" pause
endlocal

