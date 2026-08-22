@echo off
setlocal
cd /d "%~dp0"

set "EDOPS_DEPS=%LOCALAPPDATA%\EDEngineeringCompanion\python-deps"
if exist "%EDOPS_DEPS%" set "PYTHONPATH=%EDOPS_DEPS%;%PYTHONPATH%"

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller is missing. Installing the build requirement...
    python -m pip install --user -r requirements-build.txt
    if errorlevel 1 goto :failed
)

python -m PyInstaller --noconfirm --clean ED-OPS.spec
if errorlevel 1 goto :failed

copy /y "PORTABLE_README.txt" "dist\ED-OPS\README.txt" >nul
copy /y "LICENSE" "dist\ED-OPS\LICENSE" >nul
copy /y "docs\EDOPS_User_Manual_Privacy_EN_21.163.pdf" "dist\ED-OPS\EDOPS_User_Manual_Privacy_EN_21.163.pdf" >nul
copy /y "docs\EDOPS_User_Manual_Privacy_DE_21.163.pdf" "dist\ED-OPS\EDOPS_User_Manual_Privacy_DE_21.163.pdf" >nul

if not exist "output" mkdir "output"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ED-OPS\*' -DestinationPath 'output\ED-OPS-21.163-Windows.zip' -CompressionLevel Optimal -Force"
if errorlevel 1 goto :failed

echo.
echo Portable build created in dist\ED-OPS
echo Windows Explorer compatible ZIP created in output\ED-OPS-21.163-Windows.zip
exit /b 0

:failed
echo.
echo ED-OPS Windows build failed.
exit /b 1
