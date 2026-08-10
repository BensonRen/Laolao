@echo off
REM ============================================================================
REM  Stop Laolao (Windows on ARM64 / Snapdragon).
REM  Shuts down the caption engine and the virtual camera, and hands your
REM  normal webcam back to other apps. Safe to run when nothing is running.
REM ============================================================================
setlocal
title Laolao - stop
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0docs\snapdragon\launch.ps1" -Stop %*
set "LAOLAO_EXIT=%ERRORLEVEL%"

if not defined LAOLAO_NO_PAUSE pause
exit /b %LAOLAO_EXIT%
