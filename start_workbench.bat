@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONPATH=%CD%
set PYTHONIOENCODING=utf-8

REM API
start "cutoff-api" cmd /c "python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload"
timeout /t 2 /nobreak >nul

REM UI：优先 npm；本机若只有 node.exe（无 npm.cmd）则直接跑本地 vite
cd web
where npm >nul 2>&1
if %ERRORLEVEL%==0 (
  start "cutoff-workbench" cmd /c "npm run dev -- --host 127.0.0.1 --port 5173"
) else (
  if exist "C:\Program Files\nodejs\node.exe" (
    start "cutoff-workbench" cmd /c "\"C:\Program Files\nodejs\node.exe\" node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173"
  ) else (
    start "cutoff-workbench" cmd /c "node node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173"
  )
)

echo API http://127.0.0.1:8000
echo UI  http://127.0.0.1:5173
