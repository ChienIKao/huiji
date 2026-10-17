@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
    echo 第一次執行：建立環境並安裝套件...
    python -m venv .venv || goto :error
    .venv\Scripts\python -m pip install -q -r requirements.txt || goto :error
)
start "" .venv\Scripts\pythonw.exe main.py
exit /b
:error
echo 安裝失敗，請確認已安裝 Python 3.10 以上。
pause
