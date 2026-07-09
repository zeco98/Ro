# -*- coding: utf-8 -*-
"""بوت تيليجرام للمندوبين — يعمل بأسلوب Long Polling من نفس الجهاز، بلا استضافة.

كل طلب جديد يُبث لكل المندوبين المرتبطين. أول من يضغط «استلام الطلب» يستلمه،
وتُحدَّث الرسالة عند بقية المندوبين ليظهر اسم من استلمه، وتبقى بقية الطلبات
المفتوحة ظاهرة عندهم مع مناطقها (وأمر «الطلبات» يعرضها في أي وقت).
"""
import threading
import time

import requests

from . import db

CANCEL_REASONS = ['المنزل مغلق', 'الزبون ألغى', 'لا يوجد خزان', 'سبب آخر']
OPEN_LIST_LIMIT = 10

_offset = 0


def api(method, **params):
    token = db.get_setting('bot_token').strip()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        resp = requests.post(url, json=params, timeout=80)
        return resp.json()
    except requests.RequestException:
        return None


def order_text(o):
    lines = [f"📦 طلب تعبئة #{o['id']}"]
    if o['tank_number']:
        cap = f" ({o['capacity']} لتر)" if o['capacity'] else ''
        lines.append(f"🛢 الخزان: {o['tank_number']}{cap}")
    lines.append(f"👤 {o['customer_name']}")
    place = ' — '.join(x for x in [o['area'], o['street']] if x)
    if place:
        lines.append(f"📍 {place}")
    if o['landmark']:
        lines.append(f"🏷 أقرب نقطة دالة: {o['landmark']}")
    if o['customer_phone']:
        lines.append(f"📞 {o['customer_phone']}")
    if o['notes']:
        lines.append(f"📝 {o['notes']}")
    lines.append(f"🕒 {o['created_at']}")
    if o['status'] == 'claimed':
        lines.append(f"🚚 المندوب: {o['driver_name']}")
    elif o['status'] == 'delivered':
        lines.append(f"✅ تم التسليم — {o['delivered_at']} ({o['driver_name'] or 'الإدارة'})")
    elif o['status'] == 'canceled':
        lines.append(f"❌ ملغي — {o['cancel_reason'] or ''}")
    elif o['status'] == 'postponed':
        lines.append("⏳ مؤجل")
    return '\n'.join(lines)


def _map_row(o):
    url = (o['maps_url'] or '').strip()
    if url.startswith('http'):
        return [{'text': '🗺 ابدأ الملاحة', 'url': url}]
    return None


def order_keyboard(o, viewer_chat_id):
    rows = []
    if o['status'] == 'open':
        rows.append([{'text': '✅ استلام الطلب', 'callback_data': f"claim:{o['id']}"}])
    elif o['status'] == 'claimed' and viewer_chat_id == o['driver_chat_id']:
        rows.append([{'text': '✅ تم التعبئة', 'callback_data': f"done:{o['id']}"}])
        rows.append([{'text': '⏳ تأجيل', 'callback_data': f"pp:{o['id']}"},
                     {'text': '❌ إلغاء', 'callback_data': f"cancel:{o['id']}"}])
    map_row = _map_row(o)
    if map_row:
        rows.append(map_row)
    return {'inline_keyboard': rows} if rows else None


def broadcast_order(order_id):
    """إرسال الطلب لكل المندوبين المرتبطين بالبوت. يعيد عدد من وصلتهم الرسالة."""
    conn = db.get_conn()
    try:
        o = db.order_full(conn, order_id)
        if not o:
            return 0
        drivers = conn.execute(
            "SELECT * FROM drivers WHERE active=1 AND telegram_chat_id IS NOT NULL"
        ).fetchall()
        sent = 0
        for drv in drivers:
            res = api('sendMessage', chat_id=drv['telegram_chat_id'],
                      text=order_text(o),
                      reply_markup=order_keyboard(o, drv['telegram_chat_id']))
            if res and res.get('ok'):
                conn.execute(
                    "INSERT INTO order_messages(order_id, chat_id, message_id) VALUES(?,?,?)",
                    (order_id, drv['telegram_chat_id'], res['result']['message_id']))
                sent += 1
        conn.commit()
        return sent
    finally:
        conn.close()


def update_order_messages(conn, order_id):
    """تحديث نسخ رسالة الطلب عند كل المندوبين بعد أي تغيير في حالته."""
    o = db.order_full(conn, order_id)
    if not o:
        return
    msgs = conn.execute("SELECT * FROM order_messages WHERE order_id=?", (order_id,)).fetchall()
    for m in msgs:
        api('editMessageText', chat_id=m['chat_id'], message_id=m['message_id'],
            text=order_text(o), reply_markup=order_keyboard(o, m['chat_id']))


def notify_order_change(order_id):
    conn = db.get_conn()
    try:
        update_order_messages(conn, order_id)
    finally:
        conn.close()


def _answer(cb_id, text=None):
    params = {'callback_query_id': cb_id}
    if text:
        params['text'] = text
    api('answerCallbackQuery', **params)


def open_orders_message(conn):
    rows = conn.execute(db.ORDER_FULL_SQL + " WHERE o.status='open' ORDER BY o.id").fetchall()
    if not rows:
        return "لا توجد طلبات مفتوحة حاليًا ✅", None
    shown = rows[:OPEN_LIST_LIMIT]
    lines = [f"🗒 الطلبات المفتوحة ({len(rows)}):", '']
    buttons = []
    for o in shown:
        cap = f" — {o['capacity']} لتر" if o['capacity'] else ''
        lines.append(f"#{o['id']} • {o['area'] or 'بلا منطقة'} • {o['customer_name']}{cap}")
        buttons.append([{'text': f"✅ استلام #{o['id']} ({o['area'] or '—'})",
                         'callback_data': f"claim:{o['id']}"}])
    if len(rows) > OPEN_LIST_LIMIT:
        lines.append(f"\n… و{len(rows) - OPEN_LIST_LIMIT} طلبات أخرى")
    return '\n'.join(lines), {'inline_keyboard': buttons}


def handle_callback(cb):
    chat_id = cb['message']['chat']['id']
    message_id = cb['message']['message_id']
    data = cb.get('data', '')
    parts = data.split(':')
    action = parts[0]
    conn = db.get_conn()
    try:
        drv = conn.execute("SELECT * FROM drivers WHERE telegram_chat_id=? AND active=1",
                           (chat_id,)).fetchone()
        if not drv:
            _answer(cb['id'], 'أنت غير مسجل كمندوب. اطلب رمز الربط من الإدارة.')
            return
        if action == 'claim':
            oid = int(parts[1])
            now = db.now_str()
            cur = conn.execute(
                "UPDATE orders SET status='claimed', driver_id=?, claimed_at=? "
                "WHERE id=? AND status='open'", (drv['id'], now, oid))
            conn.commit()
            if cur.rowcount == 0:
                o = db.order_full(conn, oid)
                taken_by = o['driver_name'] if o else ''
                _answer(cb['id'], f'⛔ سبق أن استُلم هذا الطلب ({taken_by})')
                update_order_messages(conn, oid)
            else:
                _answer(cb['id'], f'✅ الطلب #{oid} صار باسمك')
                update_order_messages(conn, oid)
                # إن لم تكن للمندوب نسخة من رسالة الطلب (استلمه من قائمة الطلبات)
                # نرسل له بطاقة الطلب بأزرار الإنجاز
                has_copy = conn.execute(
                    "SELECT 1 FROM order_messages WHERE order_id=? AND chat_id=?",
                    (oid, chat_id)).fetchone()
                if not has_copy:
                    o = db.order_full(conn, oid)
                    res = api('sendMessage', chat_id=chat_id, text=order_text(o),
                              reply_markup=order_keyboard(o, chat_id))
                    if res and res.get('ok'):
                        conn.execute(
                            "INSERT INTO order_messages(order_id, chat_id, message_id) "
                            "VALUES(?,?,?)", (oid, chat_id, res['result']['message_id']))
                        conn.commit()
        elif action == 'done':
            oid = int(parts[1])
            o = db.order_full(conn, oid)
            if not o or o['driver_id'] != drv['id'] or o['status'] != 'claimed':
                _answer(cb['id'], 'هذا الطلب لم يعد باسمك.')
                return
            db.mark_delivered(conn, oid)
            _answer(cb['id'], '✅ تم تسجيل التعبئة، شكرًا!')
            update_order_messages(conn, oid)
        elif action == 'cancel':
            oid = int(parts[1])
            o = db.order_full(conn, oid)
            if not o or o['driver_id'] != drv['id'] or o['status'] != 'claimed':
                _answer(cb['id'], 'هذا الطلب لم يعد باسمك.')
                return
            rows = [[{'text': reason, 'callback_data': f"reason:{oid}:{i}"}]
                    for i, reason in enumerate(CANCEL_REASONS)]
            rows.append([{'text': '↩ رجوع', 'callback_data': f"back:{oid}"}])
            api('editMessageReplyMarkup', chat_id=chat_id, message_id=message_id,
                reply_markup={'inline_keyboard': rows})
            _answer(cb['id'], 'اختر سبب الإلغاء')
        elif action == 'reason':
            oid, idx = int(parts[1]), int(parts[2])
            reason = CANCEL_REASONS[idx] if 0 <= idx < len(CANCEL_REASONS) else 'سبب آخر'
            cur = conn.execute(
                "UPDATE orders SET status='canceled', cancel_reason=? "
                "WHERE id=? AND driver_id=? AND status='claimed'", (reason, oid, drv['id']))
            conn.commit()
            if cur.rowcount:
                _answer(cb['id'], '❌ أُلغي الطلب وأُبلغت الإدارة')
            else:
                _answer(cb['id'], 'هذا الطلب لم يعد باسمك.')
            update_order_messages(conn, oid)
        elif action == 'back':
            oid = int(parts[1])
            o = db.order_full(conn, oid)
            if o:
                api('editMessageReplyMarkup', chat_id=chat_id, message_id=message_id,
                    reply_markup=order_keyboard(o, chat_id))
            _answer(cb['id'])
        elif action == 'pp':
            oid = int(parts[1])
            cur = conn.execute(
                "UPDATE orders SET status='postponed' "
                "WHERE id=? AND driver_id=? AND status='claimed'", (oid, drv['id']))
            conn.commit()
            if cur.rowcount:
                _answer(cb['id'], '⏳ أُجّل الطلب')
            else:
                _answer(cb['id'], 'هذا الطلب لم يعد باسمك.')
            update_order_messages(conn, oid)
        else:
            _answer(cb['id'])
    finally:
        conn.close()


def handle_message(msg):
    chat_id = msg['chat']['id']
    text = (msg.get('text') or '').strip()
    if not text:
        return
    conn = db.get_conn()
    try:
        drv = conn.execute("SELECT * FROM drivers WHERE telegram_chat_id=?",
                           (chat_id,)).fetchone()
        if text.startswith('/start'):
            if drv:
                api('sendMessage', chat_id=chat_id,
                    text=f"أهلًا {drv['name']} 👋\nأرسل «الطلبات» لعرض الطلبات المفتوحة.")
            else:
                api('sendMessage', chat_id=chat_id,
                    text="أهلًا بك 👋\nأرسل رمز الربط الذي أعطتك إياه الإدارة لتسجيلك كمندوب.")
            return
        if not drv:
            code = text.upper()
            match = conn.execute(
                "SELECT * FROM drivers WHERE link_code=? AND telegram_chat_id IS NULL",
                (code,)).fetchone()
            if match:
                conn.execute("UPDATE drivers SET telegram_chat_id=? WHERE id=?",
                             (chat_id, match['id']))
                conn.commit()
                api('sendMessage', chat_id=chat_id,
                    text=f"✅ تم ربطك بنجاح يا {match['name']}!\n"
                         "ستصلك الطلبات الجديدة هنا، وأرسل «الطلبات» في أي وقت لعرض المفتوح منها.")
            else:
                api('sendMessage', chat_id=chat_id,
                    text="الرمز غير صحيح. اطلب رمز الربط من الإدارة ثم أرسله هنا.")
            return
        if text in ('/orders', 'الطلبات', 'طلبات'):
            body, kb = open_orders_message(conn)
            api('sendMessage', chat_id=chat_id, text=body, reply_markup=kb)
        elif text in ('طلباتي', '/my'):
            rows = conn.execute(
                db.ORDER_FULL_SQL + " WHERE o.driver_id=? AND o.status='claimed' ORDER BY o.id",
                (drv['id'],)).fetchall()
            if not rows:
                api('sendMessage', chat_id=chat_id, text='لا توجد طلبات باسمك حاليًا.')
            else:
                for o in rows:
                    api('sendMessage', chat_id=chat_id, text=order_text(o),
                        reply_markup=order_keyboard(o, chat_id))
        else:
            api('sendMessage', chat_id=chat_id,
                text='الأوامر المتاحة:\n«الطلبات» — عرض الطلبات المفتوحة\n«طلباتي» — الطلبات التي باسمك')
    finally:
        conn.close()


def poll_loop():
    global _offset
    while True:
        token = db.get_setting('bot_token').strip()
        if not token:
            time.sleep(15)
            continue
        res = api('getUpdates', offset=_offset, timeout=50,
                  allowed_updates=['message', 'callback_query'])
        if not res or not res.get('ok'):
            time.sleep(5)
            continue
        for update in res.get('result', []):
            _offset = update['update_id'] + 1
            try:
                if 'callback_query' in update:
                    handle_callback(update['callback_query'])
                elif 'message' in update:
                    handle_message(update['message'])
            except Exception:
                # لا نوقف البوت بسبب تحديث واحد معطوب
                pass


def start():
    thread = threading.Thread(target=poll_loop, daemon=True, name='telegram-bot')
    thread.start()
    return thread
