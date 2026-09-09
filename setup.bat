@echo off
REM Laolao setup script (Windows x64)
REM
REM Windows on ARM64 (Snapdragon) does NOT use this file - double-click
REM Laolao-arm64.bat instead. It maintains its own .venv-arm64 because two of
REM the packages below have no ARM64 build.
cd /d "%~dp0"

echo === Laolao Setup ===
echo.

REM ---------------------------------------------------------------- Python ---
REM 3.10 is the real floor, not 3.9: pyvirtualcam publishes no wheel below it,
REM and pip aborts the WHOLE install when one requirement is unsatisfiable - so
REM an older interpreter used to fail here having installed nothing at all.
REM
REM Prefer the Python Launcher (py) so a specific version can be asked for by
REM name, the way setup.sh does on macOS. Fall back to whatever python is on
REM PATH only if it is new enough.
set "PYTHON="
for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PYTHON (
        py -%%V -c "import sys" >nul 2>&1 && set "PYTHON=py -%%V"
    )
)
if not defined PYTHON (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYTHON=python"
)
if not defined PYTHON (
    echo ERROR: Python 3.10 or newer is required and none was found.
    echo        Install from https://www.python.org/downloads/windows/
    echo        and tick "Add python.exe to PATH" in the installer.
    pause
    exit /b 1
)
for /f "delims=" %%I in ('%PYTHON% -c "import sys; print(\"%%d.%%d\" %% sys.version_info[:2])"') do set "PY_VERSION=%%I"
echo Python: %PYTHON% (%PY_VERSION%)

REM ------------------------------------------------------------------ venv ---
if not exist venv (
    echo Creating virtual environment...
    %PYTHON% -m venv venv
    if errorlevel 1 (
        echo ERROR: could not create the virtual environment.
        pause
        exit /b 1
    )
)
call venv\Scripts\activate.bat

echo Upgrading pip...
python -m pip install --quiet --upgrade pip

REM --------------------------------------------------------------- CUDA GPU ---
python -c "import subprocess; r=subprocess.run(['nvidia-smi'], capture_output=True); exit(0 if r.returncode==0 else 1)" 2>nul
if not errorlevel 1 (
    echo NVIDIA GPU detected - installing torch+cuda...
    pip install torch --index-url https://download.pytorch.org/whl/cu121 --quiet
)

REM ---------------------------------------------------------- dependencies ---
echo Installing dependencies from requirements.txt ...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. Nothing above this line was installed.
    pause
    exit /b 1
)

echo.
echo === Setup complete! ===
echo.
echo Next steps:
echo   1. Install OBS Studio 28+ if you have not (it provides the camera driver)
echo   2. Build the app:   cd electron  ^&^&  npm install  ^&^&  npm run build:win
echo      - or run the engine alone with run.bat and use overlay\index.html as an
echo        OBS browser source (Width 1920, Height 1080)
echo   3. Select "OBS Virtual Camera" in your video call app
echo.
echo Configuration: edit config.json to adjust model, language, etc.
echo List microphones: run.bat --list-devices
echo.
pause
