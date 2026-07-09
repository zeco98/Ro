@echo off
chcp 65001 >nul
title نظام إدارة معمل ماء RO
cd /d "%~dp0"

rem تنصيب المكتبات تلقائيًا في أول تشغيل فقط
python -c "import flask, requests, webview" >nul 2>&1
if errorlevel 1 (
    echo جاري تنصيب المكتبات لأول مرة... انتظر قليلًا
    python -m pip install -r requirements.txt
)

python desktop.py
if errorlevel 1 pause
