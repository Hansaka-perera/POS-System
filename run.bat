@echo off
cd /d %~dp0
if exist .venv\Scripts\activate.bat (
  call .venv\Scripts\activate.bat
)
start "" /B python app.py
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:5000
