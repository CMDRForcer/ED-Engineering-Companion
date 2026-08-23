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

python -m PyInstaller --noconfirm --clean EDEC.spec
if errorlevel 1 goto :failed

copy /y "PORTABLE_README.txt" "dist\EDEC\README.txt" >nul
copy /y "LICENSE" "dist\EDEC\LICENSE" >nul
copy /y "docs\EDEC_User_Manual_Privacy_EN_21.163.pdf" "dist\EDEC\EDEC_User_Manual_Privacy_EN_21.163.pdf" >nul
copy /y "docs\EDEC_User_Manual_Privacy_DE_21.163.pdf" "dist\EDEC\EDEC_User_Manual_Privacy_DE_21.163.pdf" >nul

if not exist "output" mkdir "output"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\EDEC\*' -DestinationPath 'output\EDEC-21.163-Windows.zip' -CompressionLevel Optimal -Force"
if errorlevel 1 goto :failed

echo.
echo Portable build created in dist\EDEC
echo Windows Explorer compatible ZIP created in output\EDEC-21.163-Windows.zip
exit /b 0

:failed
echo.
echo EDEC Windows build failed.
exit /b 1
