# -*- coding: utf-8 -*-
"""اختبار شامل للنظام كاملًا: python tests.py

يفحص: الدخول، كل الصفحات، كل أزرار الإدارة، حسابات الوقت (30 يومًا)،
بوت خاص لكل مندوب، اختيار المندوب عند إرسال الطلب، كل أزرار البوت
(استلام/تسابق/تعبئة/إلغاء/تأجيل/تحديث الموقع)، ربط المندوبين،
النسخ الاحتياطي، وسلامة البيانات (WAL + منع الحذف الخاطئ).
"""
import datetime
import sqlite3
import sys
import tempfile
from pathlib import Path

from app import db

# قاعدة بيانات مؤقتة حتى لا نمس بيانات المحل الحقيقية
_tmp = tempfile.mkdtemp()
db.DB_PATH = Path(_tmp) / 'test.db'
db.BASE_DIR = Path(_tmp)
db.init()

from app import bot  # noqa: E402
from app.web import create_app  # noqa: E402

PASSED = 0


def check(name, cond, extra=''):
    global PASSED
    if not cond:
        print(f'❌ فشل: {name} {extra}')
        sys.exit(1)
    PASSED += 1
    print(f'✅ {name}')


app = create_app()
client = app.test_client()


def page(path):
    return client.get(path, follow_redirects=True).get_data(as_text=True)


# ---------- 1. الدخول ----------
r = client.get('/', follow_redirects=True)
check('إعادة التوجيه لصفحة الدخول قبل تسجيل الدخول', 'كلمة المرور' in r.get_data(as_text=True))
r = client.post('/login', data={'password': 'خطأ'}, follow_redirects=True)
check('رفض كلمة مرور خاطئة', 'غير صحيحة' in r.get_data(as_text=True))
r = client.post('/login', data={'password': 'admin'}, follow_redirects=True)
check('قبول كلمة المرور الصحيحة', 'لوحة التحكم' in r.get_data(as_text=True))

# ---------- 2. المشتركون ----------
r = client.post('/customers/new', data={
    'name': 'أحمد علي', 'phone': '07701234567', 'governorate': 'بغداد',
    'area': 'حي الجامعة', 'street': 'شارع 14', 'landmark': 'قرب الجامع',
    'maps_url': 'https://maps.google.com/?q=33.3,44.4', 'sub_no': '1001',
    'sub_start_date': '2026-05-01', 'notes': ''}, follow_redirects=True)
check('إضافة مشترك', 'تمت إضافة المشترك' in r.get_data(as_text=True))
r = client.post('/customers/new', data={'name': ''}, follow_redirects=True)
check('رفض مشترك بلا اسم', 'مطلوب' in r.get_data(as_text=True))
check('المشترك يظهر في القائمة', 'أحمد علي' in page('/customers'))
check('البحث عن مشترك بالهاتف', 'أحمد علي' in page('/customers?q=0770'))
check('بحث بلا نتائج لا يعطل الصفحة', 'أحمد علي' not in page('/customers?q=غيرموجود'))
r = client.post('/customers/1/edit', data={
    'name': 'أحمد علي', 'phone': '07701234567', 'governorate': 'بغداد',
    'area': 'حي الجامعة', 'street': 'شارع 14', 'landmark': 'قرب الجامع',
    'maps_url': 'https://maps.google.com/?q=33.3,44.4', 'sub_no': '1001',
    'sub_start_date': '2026-05-01', 'sub_status': 'active', 'notes': ''},
    follow_redirects=True)
check('تعديل مشترك', 'تم حفظ التعديلات' in r.get_data(as_text=True))

# ---------- 3. الخزانات ----------
r = client.post('/tanks/new', data={
    'number': '40125', 'capacity': '500', 'status': 'with_customer',
    'customer_id': '1', 'delivered_date': '2026-05-01'}, follow_redirects=True)
check('إضافة خزان', 'تمت إضافة الخزان' in r.get_data(as_text=True))
r = client.post('/tanks/new', data={'number': '40125', 'capacity': '400',
                                    'status': 'available'}, follow_redirects=True)
check('رفض رقم خزان مكرر', 'مستخدم مسبقًا' in r.get_data(as_text=True))
check('الخزان يظهر مع اسم الزبون', 'أحمد علي' in page('/tanks?q=40125'))
check('البحث برقم الخزان يصل للمشترك', 'أحمد علي' in page('/customers?q=40125'))

# ---------- 4. مندوبون ببوتات خاصة وربط تيليجرام ----------
TOK1, TOK2 = 'TOKEN-HASAN-111', 'TOKEN-KARAR-222'
r = client.post('/drivers/new', data={'name': 'حسن', 'phone': '0780', 'car': 'كيا',
                                      'areas': 'حي الجامعة', 'bot_token': TOK1},
                follow_redirects=True)
check('إضافة مندوب مع بوت خاص', 'أُضيف المندوب' in r.get_data(as_text=True))
client.post('/drivers/new', data={'name': 'كرار', 'car': 'هيونداي', 'bot_token': TOK2},
            follow_redirects=True)
conn = db.get_conn()
code1 = conn.execute("SELECT link_code FROM drivers WHERE id=1").fetchone()[0]
code2 = conn.execute("SELECT link_code FROM drivers WHERE id=2").fetchone()[0]
check('حفظ رمز البوت الخاص في بطاقة المندوب',
      conn.execute("SELECT bot_token FROM drivers WHERE id=1").fetchone()[0] == TOK1)
conn.close()
check('توليد رمز ربط للمندوب', code1 and len(code1) == 6)
check('النظام يرصد بوتَي المندوبَين للتشغيل', bot.all_tokens() == {TOK1, TOK2})
check('صفحة المندوبين تعرض «بوت خاص»', 'بوت خاص' in page('/drivers'))

# محاكاة تيليجرام بدون إنترنت — نسجل الرسائل مع رمز البوت المرسل عبره
sent_messages = []
bot.api = lambda method, token=None, **p: (
    sent_messages.append((method, token, p)),
    {'ok': True, 'result': {'message_id': len(sent_messages)}})[1]
answers = []
bot._answer = lambda cb_id, token=None, text=None: answers.append(text or '')

bot.handle_message({'chat': {'id': 111}, 'text': '/start'}, TOK1)
check('البوت الخاص يطلب رمز الربط من مجهول', 'رمز الربط' in sent_messages[-1][2]['text'])
bot.handle_message({'chat': {'id': 111}, 'text': 'xxxxxx'}, TOK1)
check('رفض رمز ربط خاطئ', 'غير صحيح' in sent_messages[-1][2]['text'])
bot.handle_message({'chat': {'id': 111}, 'text': code2.lower()}, TOK1)
check('رمز مندوب آخر لا يعمل على هذا البوت', 'غير صحيح' in sent_messages[-1][2]['text'])
bot.handle_message({'chat': {'id': 111}, 'text': code1.lower()}, TOK1)
conn = db.get_conn()
linked = conn.execute("SELECT telegram_chat_id FROM drivers WHERE id=1").fetchone()[0]
conn.close()
check('ربط المندوب ببوته الخاص (رمز بأحرف صغيرة)', linked == 111)
bot.handle_message({'chat': {'id': 999}, 'text': code1}, TOK1)
check('شخص غريب وجد البوت: يُرفض لأن البوت مخصص لمندوب آخر',
      'مخصص لمندوب آخر' in sent_messages[-1][2]['text'])
bot.handle_message({'chat': {'id': 222}, 'text': code2}, TOK2)
conn = db.get_conn()
check('ربط المندوب الثاني ببوته',
      conn.execute("SELECT telegram_chat_id FROM drivers WHERE id=2").fetchone()[0] == 222)
conn.close()

# ---------- 5. إنشاء طلب وبثه — كل مندوب عبر بوته الخاص ----------
t = page('/orders/new?q=أحمد')
check('البحث في إنشاء طلب يعرض المشترك وخزانه', 'أحمد علي' in t and '40125' in t)
check('نموذج الإنشاء يعرض اختيار المندوب/السيارة',
      'كل المندوبين' in t and 'كيا' in t and 'هيونداي' in t)
sent_messages.clear()
r = client.post('/orders/create', data={'customer_id': '1', 'tank_id': '1',
                                        'notes': 'عجل'}, follow_redirects=True)
check('إنشاء طلب وبثه لمندوبَين', 'أُرسل إلى 2 مندوب' in r.get_data(as_text=True))
broadcast = [m for m in sent_messages if m[0] == 'sendMessage']
tokens_used = {m[1] for m in broadcast}
check('كل مندوب استلم الطلب عبر بوته الخاص هو', tokens_used == {TOK1, TOK2})
check('رسالة البث تحتوي بيانات الطلب كاملة',
      all(x in broadcast[0][2]['text'] for x in
          ('أحمد علي', '40125', '500', 'حي الجامعة', 'قرب الجامع', '0770', 'عجل')))
check('زر استلام الطلب موجود في الرسالة',
      broadcast[0][2]['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'claim:1')
check('زر الملاحة موجود عند وجود رابط خريطة',
      any('url' in b for row in broadcast[0][2]['reply_markup']['inline_keyboard'] for b in row))

# ---------- 6. إرسال طلب لمندوب محدد (سيارة/صاحب هاتف) ----------
sent_messages.clear()
r = client.post('/orders/create', data={'customer_id': '1', 'tank_id': '1',
                                        'driver_id': '2'}, follow_redirects=True)
check('الطلب أُرسل للمندوب المختار فقط', 'أُرسل إلى المندوب كرار' in r.get_data(as_text=True))
targeted = [m for m in sent_messages if m[0] == 'sendMessage']
check('رسالة واحدة فقط وعبر بوت المندوب المختار',
      len(targeted) == 1 and targeted[0][1] == TOK2 and targeted[0][2]['chat_id'] == 222)
client.post('/orders/2/cancel', data={'reason': 'تجربة'}, follow_redirects=True)


def cb(chat, data):
    return {'id': 'x', 'data': data, 'message': {'chat': {'id': chat}, 'message_id': 7}}


# ---------- 7. أزرار البوت: استلام وتسابق ----------
answers.clear()
bot.handle_callback(cb(111, 'claim:1'), TOK1)
bot.handle_callback(cb(222, 'claim:1'), TOK2)
check('أول من يضغط استلام يفوز', 'صار باسمك' in answers[0])
check('الثاني تظهر له رسالة أن الطلب استُلم مع الاسم',
      'سبق أن استُلم' in answers[1] and 'حسن' in answers[1])
conn = db.get_conn()
o = db.order_full(conn, 1)
conn.close()
check('الطلب صار «بالطريق» باسم المندوب الأول',
      o['status'] == 'claimed' and o['driver_name'] == 'حسن')
answers.clear()
bot.handle_callback(cb(999, 'claim:1'), TOK1)
check('غريب يضغط زرًا على بوت مندوب: يُرفض', 'مخصص لمندوب آخر' in answers[-1])

# ---------- 8. زر تحديث الموقع: المندوب يرسل موقعه فيتحدث موقع المشترك ----------
sent_messages.clear()
answers.clear()
bot.handle_callback(cb(222, 'loc:1'), TOK2)
check('غير صاحب الطلب لا يستطيع تحديث الموقع', 'لم يعد باسمك' in answers[-1])
bot.handle_callback(cb(111, 'loc:1'), TOK1)
loc_req = sent_messages[-1]
check('طلب الموقع يُرسل بزر «إرسال موقعي الحالي»',
      loc_req[0] == 'sendMessage' and
      loc_req[2]['reply_markup']['keyboard'][0][0].get('request_location') is True)
conn = db.get_conn()
check('النظام سجل أن المندوب بانتظار إرسال موقع للطلب 1',
      conn.execute("SELECT pending_loc_order FROM drivers WHERE id=1").fetchone()[0] == 1)
conn.close()
sent_messages.clear()
bot.handle_message({'chat': {'id': 111},
                    'location': {'latitude': 33.312805, 'longitude': 44.361488}}, TOK1)
conn = db.get_conn()
new_url = conn.execute("SELECT maps_url FROM customers WHERE id=1").fetchone()[0]
pending = conn.execute("SELECT pending_loc_order FROM drivers WHERE id=1").fetchone()[0]
conn.close()
check('موقع المشترك تحدّث تلقائيًا من موقع تيليجرام',
      new_url == 'https://maps.google.com/?q=33.312805,44.361488')
check('حالة انتظار الموقع صُفّرت بعد الحفظ', pending is None)
check('المندوب وصله تأكيد الحفظ وأُزيلت لوحة الموقع',
      'تم تحديث موقع المشترك' in sent_messages[0][2]['text'] and
      sent_messages[0][2]['reply_markup'] == {'remove_keyboard': True})
check('الموقع الجديد ظهر في صفحة المشتركين في النظام',
      '33.312805' in page('/customers/1/edit'))
# إلغاء طلب الموقع
bot.handle_callback(cb(111, 'loc:1'), TOK1)
bot.handle_message({'chat': {'id': 111}, 'text': 'إلغاء'}, TOK1)
conn = db.get_conn()
check('«إلغاء» يوقف طلب الموقع',
      conn.execute("SELECT pending_loc_order FROM drivers WHERE id=1").fetchone()[0] is None)
conn.close()

# ---------- 9. زر تم التعبئة ----------
answers.clear()
bot.handle_callback(cb(222, 'done:1'), TOK2)
check('غير صاحب الطلب لا يستطيع إنهاءه', 'لم يعد باسمك' in answers[-1])
bot.handle_callback(cb(111, 'done:1'), TOK1)
conn = db.get_conn()
o = db.order_full(conn, 1)
tank = conn.execute("SELECT * FROM tanks WHERE id=1").fetchone()
conn.close()
check('تم التسليم مع تسجيل الوقت', o['status'] == 'delivered' and o['delivered_at'])
check('تحديث الخزان: آخر تعبئة اليوم وعداد +1',
      tank['fill_count'] == 1 and tank['last_fill_date'] == datetime.date.today().isoformat())

# ---------- 10. زر الإلغاء مع الأسباب ----------
client.post('/orders/create', data={'customer_id': '1', 'tank_id': '1'}, follow_redirects=True)
bot.handle_callback(cb(222, 'claim:3'), TOK2)
sent_messages.clear()
bot.handle_callback(cb(222, 'cancel:3'), TOK2)
reasons_kb = sent_messages[-1][2]['reply_markup']['inline_keyboard']
check('نافذة أسباب الإلغاء تعرض 4 أسباب + رجوع', len(reasons_kb) == 5)
bot.handle_callback(cb(222, 'reason:3:1'), TOK2)
conn = db.get_conn()
o = db.order_full(conn, 3)
conn.close()
check('الإلغاء بسبب «الزبون ألغى» وصل لقاعدة البيانات',
      o['status'] == 'canceled' and o['cancel_reason'] == 'الزبون ألغى')
check('سبب الإلغاء يظهر للإدارة في صفحة الطلبات', 'الزبون ألغى' in page('/orders'))

# ---------- 11. زر التأجيل وإعادة الإرسال والأزرار اليدوية ----------
client.post('/orders/create', data={'customer_id': '1', 'tank_id': '1'}, follow_redirects=True)
bot.handle_callback(cb(111, 'claim:4'), TOK1)
bot.handle_callback(cb(111, 'pp:4'), TOK1)
conn = db.get_conn()
check('التأجيل من البوت', conn.execute(
    "SELECT status FROM orders WHERE id=4").fetchone()[0] == 'postponed')
conn.close()
r = client.post('/orders/4/resend', follow_redirects=True)
conn = db.get_conn()
row = conn.execute("SELECT status, driver_id FROM orders WHERE id=4").fetchone()
conn.close()
check('إعادة إرسال المؤجل: يعود مفتوحًا بلا مندوب',
      row['status'] == 'open' and row['driver_id'] is None)
r = client.post('/orders/4/deliver', follow_redirects=True)
check('تسليم يدوي من الإدارة', 'سُجل تسليم' in r.get_data(as_text=True))
client.post('/orders/create', data={'customer_id': '1'}, follow_redirects=True)
r = client.post('/orders/5/cancel', data={'reason': 'تجربة'}, follow_redirects=True)
check('إلغاء يدوي من الإدارة', 'أُلغي الطلب' in r.get_data(as_text=True))

# ---------- 12. حسابات الوقت (قاعدة الـ30 يومًا) ----------
def set_last_fill(days_ago):
    day = (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat()
    conn = db.get_conn()
    conn.execute("UPDATE tanks SET last_fill_date=? WHERE id=1", (day,))
    conn.execute("UPDATE orders SET delivered_at=? WHERE status='delivered'", (day + ' 10:00',))
    conn.commit()
    pairs = db.customers_with_cycle(conn)
    conn.close()
    return pairs[0][1]

info = set_last_fill(10)
check('اليوم 10: نشط وباقٍ 20 يومًا', info['key'] == 'active' and info['remaining'] == 20)
info = set_last_fill(27)
check('اليوم 27: سينتهي قريبًا', info['key'] == 'soon' and info['remaining'] == 3)
info = set_last_fill(31)
check('اليوم 31: متأخر يوم واحد', info['key'] == 'late' and info['late'] == 1)
info = set_last_fill(45)
check('اليوم 45: متأخر 15 يومًا', info['key'] == 'late' and info['late'] == 15)
check('المتأخر يظهر في صفحة المتأخرين', 'متأخر 15 يوم' in page('/late'))
conn = db.get_conn()
conn.execute("UPDATE customers SET sub_status='suspended' WHERE id=1")
conn.commit()
info = db.customers_with_cycle(conn)[0][1]
conn.execute("UPDATE customers SET sub_status='active' WHERE id=1")
conn.commit()
conn.close()
check('الاشتراك الموقوف لا يُحسب متأخرًا', info['key'] == 'suspended')

# ---------- 13. قائمة الطلبات في البوت (حد الـ10) ----------
conn = db.get_conn()
for _ in range(12):
    conn.execute("INSERT INTO orders(customer_id, tank_id, status, created_at) "
                 "VALUES(1, 1, 'open', ?)", (db.now_str(),))
conn.commit()
body, kb = bot.open_orders_message(conn)
conn.close()
check('قائمة البوت تعرض 10 طلبات كحد أقصى مع أزرار استلام',
      len(kb['inline_keyboard']) == 10 and 'أخرى' in body)
check('كل سطر يذكر منطقة الطلب', body.count('حي الجامعة') >= 10)
sent_messages.clear()
bot.handle_message({'chat': {'id': 111}, 'text': 'الطلبات'}, TOK1)
check('أمر «الطلبات» يعمل للمندوب عبر بوته',
      'الطلبات المفتوحة' in sent_messages[-1][2]['text'] and sent_messages[-1][1] == TOK1)

# ---------- 14. كل الصفحات تفتح بلا أخطاء ----------
for path in ['/', '/customers', '/customers/new', '/customers/1/edit',
             '/tanks', '/tanks/new', '/tanks/1/edit', '/orders', '/orders/new',
             '/orders/new?q=أحمد', '/orders?status=delivered', '/drivers',
             '/drivers/new', '/drivers/1/edit', '/late', '/reports', '/settings']:
    r = client.get(path)
    check(f'صفحة {path}', r.status_code == 200)

# ---------- 15. التحديث التلقائي للشاشات الحية ----------
check('لوحة التحكم تتحدث تلقائيًا كل 30 ثانية', 'location.reload' in page('/'))
check('صفحات النماذج لا تتحدث تلقائيًا (حتى لا يضيع الإدخال)',
      'location.reload' not in page('/customers/new'))

# ---------- 16. حماية البيانات ----------
conn = db.get_conn()
mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
conn.close()
check('وضع WAL فعّال (حماية من انقطاع الكهرباء)', mode.lower() == 'wal')
r = client.post('/customers/1/delete', follow_redirects=True)
check('منع حذف مشترك له طلبات (لا ضياع للسجل)',
      'لا يمكن حذف' in r.get_data(as_text=True))
conn = db.get_conn()
check('المشترك ما زال موجودًا بعد محاولة الحذف',
      conn.execute("SELECT COUNT(*) FROM customers WHERE id=1").fetchone()[0] == 1)
conn.close()

target = db.backup()
check('النسخة الاحتياطية أُنشئت', target and target.exists())
check('تكرار النسخ في نفس اليوم لا ينشئ ملفات زائدة', db.backup() == target)
src_count = sqlite3.connect(db.DB_PATH).execute("SELECT COUNT(*) FROM orders").fetchone()[0]
bak_count = sqlite3.connect(target).execute("SELECT COUNT(*) FROM orders").fetchone()[0]
check('النسخة الاحتياطية مطابقة للبيانات الحقيقية', src_count == bak_count)

# ---------- 17. الإعدادات ----------
r = client.post('/settings', data={'factory_name': 'معمل النور', 'cycle_days': '30',
                                   'warn_days': '5', 'bot_token': ''},
                follow_redirects=True)
check('حفظ الإعدادات', db.get_setting('factory_name') == 'معمل النور')
r = client.post('/settings', data={'admin_password': 'سري123'}, follow_redirects=True)
r = client.get('/logout', follow_redirects=True)
r = client.post('/login', data={'password': 'admin'}, follow_redirects=True)
check('كلمة المرور القديمة لم تعد تعمل', 'غير صحيحة' in r.get_data(as_text=True))
r = client.post('/login', data={'password': 'سري123'}, follow_redirects=True)
check('كلمة المرور الجديدة تعمل', 'لوحة التحكم' in r.get_data(as_text=True))

print()
print(f'🎉 نجحت كل الاختبارات: {PASSED} اختبارًا')
