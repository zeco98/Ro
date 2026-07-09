@echo off
chcp 65001 >nul
title بناء ملف exe للنظام
cd /d "%~dp0"

echo جاري تنصيب أداة البناء...
python -m pip install pyinstaller -q
python -m pip install -r requirements.txt -q

echo جاري بناء التطبيق... قد يستغرق دقائق
python -m PyInstaller --noconfirm --clean --windowed --name "RoWater" ^
  --add-data "app/templates;app/templates" ^
  --add-data "app/static;app/static" ^
  desktop.py

echo.
echo ✅ انتهى البناء — التطبيق في: dist\RoWater\RoWater.exe
echo انسخ مجلد dist\RoWater كاملًا لأي مكان (سطح المكتب مثلًا)
echo وشغّل RoWater.exe — البيانات data.db والنسخ الاحتياطية تُحفظ بجانبه
pause
