@echo off
cd /d "%~dp0"
echo Starting Streamlit UI ...
echo Please ensure API is running: start_api.bat
python run_ui.py
pause
