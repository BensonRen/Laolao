@echo off
REM Laolao setup script (Windows)
cd /d "%~dp0"

echo === Laolao Setup ===
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.9+ is required. Install from https://python.org
    pause
    exit /b 1
)

echo Creating virtual environment...
python -m venv venv
call venv\Scripts\activate.bat

echo Upgrading pip...
python -m pip install --quiet --upgrade pip

REM Check for CUDA GPU
python -c "import subprocess; r=subprocess.run(['nvidia-smi'], capture_output=True); exit(0 if r.returncode==0 else 1)" 2>nul
if not errorlevel 1 (
    echo NVIDIA GPU detected - installing torch+cuda...
    pip install torch --index-url https://download.pytorch.org/whl/cu121 --quiet
)

echo Installing dependencies...
pip install -r requirements.txt

echo.
echo === Setup complete! ===
echo.
echo Next steps:
echo   1. Open OBS Studio
echo   2. Add a Browser Source in your scene
echo   3. Set URL to the full path of: overlay\index.html  (use file:///C:\... format)
echo   4. Set Width: 1920, Height: 1080
echo   5. Run: run.bat
echo   6. Start OBS Virtual Camera
echo   7. Select 'OBS Virtual Camera' in your video call app
echo.
echo Configuration: edit config.json
echo List microphones: run.bat --list-devices
echo.
pause
