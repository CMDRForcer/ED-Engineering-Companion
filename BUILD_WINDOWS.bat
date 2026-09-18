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

set "EDOPS_DEPS=%LOCALAPPDATA%\ED-Frame\python-deps"
set "PYTHONPATH="
if exist "%EDOPS_DEPS%" set "PYTHONPATH=%EDOPS_DEPS%"

"%EDEC_PYTHON%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller is missing. Installing the build requirement...
    "%EDEC_PYTHON%" -m pip install --user -r requirements-build.txt
    if errorlevel 1 goto :failed
)

"%EDEC_PYTHON%" -m PyInstaller --noconfirm --clean ED-Frame.spec
if errorlevel 1 goto :failed

if exist "dist\ED-Frame\_internal\icuuc.dll" goto :contaminated
if exist "dist\ED-Frame\_internal\icudt78.dll" goto :contaminated

copy /y "PORTABLE_README.txt" "dist\ED-Frame\README.txt" >nul
copy /y "LICENSE" "dist\ED-Frame\LICENSE" >nul
copy /y "docs\ED-Frame_User_Manual_Privacy_EN_1.5.5.pdf" "dist\ED-Frame\ED-Frame_User_Manual_Privacy_EN_%EDEC_VERSION%.pdf" >nul
copy /y "docs\ED-Frame_User_Manual_Privacy_DE_1.5.5.pdf" "dist\ED-Frame\ED-Frame_User_Manual_Privacy_DE_%EDEC_VERSION%.pdf" >nul

if not exist "output" mkdir "output"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ED-Frame\*' -DestinationPath 'output\ED-Frame-%EDEC_VERSION%-Windows.zip' -CompressionLevel Optimal -Force"
if errorlevel 1 goto :failed

echo.
echo Portable build created in dist\ED-Frame
echo Windows Explorer compatible ZIP created in output\ED-Frame-%EDEC_VERSION%-Windows.zip
exit /b 0

:contaminated
echo.
echo ED-Frame Windows build contains foreign ICU DLLs from the host PATH.
echo Refusing to create a broken release archive.
exit /b 1

:failed
echo.
echo ED-Frame Windows build failed.
exit /b 1
