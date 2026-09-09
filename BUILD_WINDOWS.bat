@echo off
setlocal
cd /d "%~dp0"

for /f "delims=" %%P in ('where python 2^>nul') do if not defined EDEC_PYTHON set "EDEC_PYTHON=%%P"
if not defined EDEC_PYTHON goto :failed
for %%D in ("%EDEC_PYTHON%") do set "EDEC_PYTHON_DIR=%%~dpD"

for /f "usebackq delims=" %%V in (`python -c "from ed_companion import APP_VERSION; print(APP_VERSION)"`) do set "EDEC_VERSION=%%V"
if not defined EDEC_VERSION goto :failed

rem Keep PyInstaller from collecting unrelated DLLs injected by shells and developer tools.
set "PATH=%EDEC_PYTHON_DIR%;%EDEC_PYTHON_DIR%Scripts;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"

set "EDOPS_DEPS=%LOCALAPPDATA%\EDEngineeringCompanion\python-deps"
set "PYTHONPATH="
if exist "%EDOPS_DEPS%" set "PYTHONPATH=%EDOPS_DEPS%"

"%EDEC_PYTHON%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller is missing. Installing the build requirement...
    "%EDEC_PYTHON%" -m pip install --user -r requirements-build.txt
    if errorlevel 1 goto :failed
)

"%EDEC_PYTHON%" -m PyInstaller --noconfirm --clean EDEC.spec
if errorlevel 1 goto :failed

if exist "dist\EDEC\_internal\icuuc.dll" goto :contaminated
if exist "dist\EDEC\_internal\icudt78.dll" goto :contaminated

copy /y "PORTABLE_README.txt" "dist\EDEC\README.txt" >nul
copy /y "LICENSE" "dist\EDEC\LICENSE" >nul
copy /y "docs\EDEC_User_Manual_Privacy_EN_21.164.pdf" "dist\EDEC\EDEC_User_Manual_Privacy_EN_%EDEC_VERSION%.pdf" >nul
copy /y "docs\EDEC_User_Manual_Privacy_DE_21.164.pdf" "dist\EDEC\EDEC_User_Manual_Privacy_DE_%EDEC_VERSION%.pdf" >nul

if not exist "output" mkdir "output"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\EDEC\*' -DestinationPath 'output\EDEC-%EDEC_VERSION%-Windows.zip' -CompressionLevel Optimal -Force"
if errorlevel 1 goto :failed

echo.
echo Portable build created in dist\EDEC
echo Windows Explorer compatible ZIP created in output\EDEC-%EDEC_VERSION%-Windows.zip
exit /b 0

:contaminated
echo.
echo EDEC Windows build contains foreign ICU DLLs from the host PATH.
echo Refusing to create a broken release archive.
exit /b 1

:failed
echo.
echo EDEC Windows build failed.
exit /b 1
