@echo off
cd /d "%~dp0"
echo Starting Cutoff API on http://localhost:8000 ...
echo Docs: http://localhost:8000/docs
python run_api.py
pause
