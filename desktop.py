# -*- coding: utf-8 -*-
"""تشغيل النظام كتطبيق سطح مكتب بنافذة خاصة.

عند الفتح يعمل كل شيء تلقائيًا:
- قاعدة البيانات + نسخة احتياطية يومية
- بوت تيليجرام (Long Polling)
- خادم الويب الداخلي
- نافذة التطبيق (WebView2 على ويندوز) — وإن تعذرت تُفتح في المتصفح
"""
import socket
import threading
import time
import webbrowser

from app import bot, db

HOST = '0.0.0.0'   # يسمح بالدخول من أجهزة الشبكة المحلية أيضًا
PORT = 8000
URL = f'http://127.0.0.1:{PORT}'


def wait_for_server(timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main():
    db.init()
    db.backup()
    db.start_auto_backup(interval_hours=6)
    bot.start()

    from app.web import create_app
    app = create_app()
    server = threading.Thread(
        target=lambda: app.run(host=HOST, port=PORT, debug=False, use_reloader=False),
        daemon=True, name='web-server')
    server.start()
    wait_for_server()

    title = f"💧 {db.get_setting('factory_name', 'معمل ماء RO')} — نظام الإدارة"
    try:
        import webview
        window = webview.create_window(title, URL, width=1280, height=800,
                                       min_size=(900, 600))
        webview.start()  # يبقى التطبيق يعمل حتى إغلاق النافذة
    except Exception:
        # لا توجد مكتبة نافذة — نفتح المتصفح ونبقي النظام يعمل
        webbrowser.open(URL)
        print('=' * 50)
        print('  النظام يعمل — أغلق هذه النافذة لإيقافه')
        print(f'  الرابط: {URL}')
        print('=' * 50)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
