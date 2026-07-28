@echo off
cd /d "%~dp0"
echo Starting FastAPI on http://localhost:8000 ...
echo FastAPI 已启动，支持三单数据上传（ledger_entry + delivery_receipt）
echo Docs: http://localhost:8000/docs
python run_api.py
pause
