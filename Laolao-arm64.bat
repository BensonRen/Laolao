@echo off
REM ============================================================================
REM  Laolao - live Chinese captions on your camera
REM  Windows on ARM64 / Snapdragon.  Double-click this file. That is all.
REM
REM  It installs anything that is missing the first time, starts the caption
REM  engine and the virtual camera, and then tells you which camera to pick in
REM  WeChat or Zoom. Running it twice is safe - it reuses what is already up.
REM
REM  No administrator rights are needed.
REM  Everything it does lives in docs\snapdragon\launch.ps1.
REM ============================================================================
setlocal
title Laolao (Snapdragon ARM64)
cd /d "%~dp0"

if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" goto :arch_ok
if /i "%PROCESSOR_ARCHITEW6432%"=="ARM64" goto :arch_ok
echo.
echo  This launcher is for Windows on ARM64 (Snapdragon) PCs.
echo  This PC reports "%PROCESSOR_ARCHITECTURE%".
echo.
echo  On a normal Intel or AMD PC, use setup.bat once and then run.bat.
echo.
pause
exit /b 1

:arch_ok
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0docs\snapdragon\launch.ps1" %*
set "LAOLAO_EXIT=%ERRORLEVEL%"

REM Keep the window open so the instructions can actually be read.
REM Automated runs set LAOLAO_NO_PAUSE=1 to skip this.
if not defined LAOLAO_NO_PAUSE pause
exit /b %LAOLAO_EXIT%
