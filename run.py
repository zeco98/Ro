# -*- coding: utf-8 -*-
"""تشغيل النظام: python run.py ثم افتح http://localhost:8000"""
from app import bot, db

db.init()

from app.web import create_app  # noqa: E402  (يحتاج قاعدة بيانات مهيأة)

app = create_app()

if __name__ == '__main__':
    bot.start()
    print('=' * 50)
    print('  نظام إدارة معمل ماء RO يعمل الآن')
    print('  افتح المتصفح على: http://localhost:8000')
    print('  كلمة المرور الافتراضية: admin')
    print('=' * 50)
    # بدون وضع التطوير حتى لا يعمل البوت مرتين
    app.run(host='0.0.0.0', port=8000, debug=False)
