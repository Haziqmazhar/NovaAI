@echo off
REM Launches Nova using the virtual environment in this folder.
REM This is the file you point a Startup shortcut at (see README.md).
cd /d "%~dp0"
call venv\Scripts\activate.bat
python main.py
pause
