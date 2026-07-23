@echo off
cd /d "%~dp0"
echo Starting FastAPI on http://localhost:8000 ...
python run_api.py
pause
