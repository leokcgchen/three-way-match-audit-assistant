@echo off
cd /d "%~dp0"
echo Starting Streamlit UI ...
echo Streamlit UI 已启动，支持三单数据录入 + 截止性测试展示
echo Please ensure API is running: start_api.bat
echo UI: http://localhost:8501
python run_ui.py
pause
