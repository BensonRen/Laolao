@echo off
REM Laolao startup script (Windows)
cd /d "%~dp0"

REM Activate virtual environment if present
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Forward any extra args to server.py
python server.py %*
