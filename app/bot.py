# -*- coding: utf-8 -*-
"""بوتات تيليجرام للمندوبين — Long Polling من نفس الجهاز، بلا استضافة.

لكل مندوب بوت خاص به (Token في بطاقة المندوب)، ويوجد بوت عام احتياطي
في الإعدادات لمن ليس له بوت خاص. النظام يدير كل البوتات معًا.

- الطلب يُبث لكل المندوبين أو لمندوب محدد تختاره الإدارة عند الإنشاء.
- أول من يضغط «استلام الطلب» يستلمه ويظهر اسمه عند البقية.
- زر «تحديث الموقع»: المندوب يرسل موقعه من تيليجرام مباشرة
  فيتحدّث موقع المشترك تلقائيًا في النظام.
"""
import threading
import time

import requests

from . import db

CANCEL_REASONS = ['المنزل مغلق', 'الزبون ألغى', 'لا يوجد خزان', 'سبب آخر']
OPEN_LIST_LIMIT = 10

_pollers = {}  # token -> {'stop': bool, 'offset': int}


def api(method, token, **params):
    token = (token or '').strip()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        resp = requests.post(url, json=params, timeout=80)
        return resp.json()
    except requests.RequestException:
        return None


def global_token():
    return db.get_setting('bot_token').strip()


def driver_token(drv):
    return (drv['bot_token'] or '').strip() or global_token()


# ---------- نص الطلب وأزراره ----------

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


def order_keyboard(o, viewer_driver_id):
    rows = []
    if o['status'] == 'open':
        rows.append([{'text': '✅ استلام الطلب', 'callback_data': f"claim:{o['id']}"}])
    elif o['status'] == 'claimed' and viewer_driver_id == o['driver_id']:
        rows.append([{'text': '✅ تم التعبئة', 'callback_data': f"done:{o['id']}"}])
        rows.append([{'text': '⏳ تأجيل', 'callback_data': f"pp:{o['id']}"},
                     {'text': '❌ إلغاء', 'callback_data': f"cancel:{o['id']}"}])
        rows.append([{'text': '📍 تحديث الموقع', 'callback_data': f"loc:{o['id']}"}])
    map_row = _map_row(o)
    if map_row:
        rows.append(map_row)
    return {'inline_keyboard': rows} if rows else None


# ---------- الإرسال والتحديث ----------

def broadcast_order(order_id, only_driver_id=None):
    """بث الطلب: للجميع، أو لمندوب محدد (سيارة/صاحب هاتف) تختاره الإدارة."""
    conn = db.get_conn()
    try:
        o = db.order_full(conn, order_id)
        if not o:
            return 0
        sql = "SELECT * FROM drivers WHERE active=1 AND telegram_chat_id IS NOT NULL"
        params = []
        if only_driver_id:
            sql += " AND id=?"
            params = [only_driver_id]
        drivers = conn.execute(sql, params).fetchall()
        sent = 0
        for drv in drivers:
            res = api('sendMessage', driver_token(drv), chat_id=drv['telegram_chat_id'],
                      text=order_text(o), reply_markup=order_keyboard(o, drv['id']))
            if res and res.get('ok'):
                conn.execute(
                    "INSERT INTO order_messages(order_id, chat_id, message_id, driver_id) "
                    "VALUES(?,?,?,?)",
                    (order_id, drv['telegram_chat_id'], res['result']['message_id'], drv['id']))
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
    msgs = conn.execute(
        "SELECT m.*, d.bot_token AS drv_bot_token FROM order_messages m "
        "JOIN drivers d ON d.id = m.driver_id WHERE m.order_id=?", (order_id,)).fetchall()
    for m in msgs:
        token = (m['drv_bot_token'] or '').strip() or global_token()
        api('editMessageText', token, chat_id=m['chat_id'], message_id=m['message_id'],
            text=order_text(o), reply_markup=order_keyboard(o, m['driver_id']))


def notify_order_change(order_id):
    conn = db.get_conn()
    try:
        update_order_messages(conn, order_id)
    finally:
        conn.close()


def _answer(cb_id, token, text=None):
    params = {'callback_query_id': cb_id}
    if text:
        params['text'] = text
    api('answerCallbackQuery', token, **params)


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


# ---------- تحديد هوية المندوب ----------

def driver_for(conn, token, chat_id):
    """يحدد المندوب: البوت الخاص يعرّف صاحبه مباشرة، والبوت العام بالمحادثة."""
    token = (token or '').strip()
    if token:
        drv = conn.execute("SELECT * FROM drivers WHERE bot_token=?", (token,)).fetchone()
        if drv:
            return drv, True
    drv = conn.execute(
        "SELECT * FROM drivers WHERE telegram_chat_id=? "
        "AND (bot_token IS NULL OR bot_token='')", (chat_id,)).fetchone()
    return drv, False


def _resolve_driver(conn, token, chat_id):
    """يعيد سجل المندوب المخوّل بهذه المحادثة أو None مع سبب الرفض."""
    drv, personal = driver_for(conn, token, chat_id)
    if not drv:
        return None, 'أنت غير مسجل كمندوب. اطلب رمز الربط من الإدارة.'
    if personal:
        if not drv['telegram_chat_id']:
            return None, 'أرسل رمز الربط أولًا لتفعيل البوت.'
        if drv['telegram_chat_id'] != chat_id:
            return None, 'هذا البوت مخصص لمندوب آخر.'
    if not drv['active']:
        return None, 'حسابك موقوف حاليًا — راجع الإدارة.'
    return drv, None


# ---------- أزرار البوت ----------

def handle_callback(cb, token):
    chat_id = cb['message']['chat']['id']
    message_id = cb['message']['message_id']
    parts = cb.get('data', '').split(':')
    action = parts[0]
    conn = db.get_conn()
    try:
        drv, reject = _resolve_driver(conn, token, chat_id)
        if not drv:
            _answer(cb['id'], token, reject)
            return
        if action == 'claim':
            oid = int(parts[1])
            cur = conn.execute(
                "UPDATE orders SET status='claimed', driver_id=?, claimed_at=? "
                "WHERE id=? AND status='open'", (drv['id'], db.now_str(), oid))
            conn.commit()
            if cur.rowcount == 0:
                o = db.order_full(conn, oid)
                taken_by = o['driver_name'] if o else ''
                _answer(cb['id'], token, f'⛔ سبق أن استُلم هذا الطلب ({taken_by})')
                update_order_messages(conn, oid)
            else:
                _answer(cb['id'], token, f'✅ الطلب #{oid} صار باسمك')
                update_order_messages(conn, oid)
                # استلمه من قائمة الطلبات وليس عنده بطاقة للطلب — نرسلها له
                has_copy = conn.execute(
                    "SELECT 1 FROM order_messages WHERE order_id=? AND driver_id=?",
                    (oid, drv['id'])).fetchone()
                if not has_copy:
                    o = db.order_full(conn, oid)
                    res = api('sendMessage', driver_token(drv), chat_id=chat_id,
                              text=order_text(o), reply_markup=order_keyboard(o, drv['id']))
                    if res and res.get('ok'):
                        conn.execute(
                            "INSERT INTO order_messages(order_id, chat_id, message_id, driver_id) "
                            "VALUES(?,?,?,?)", (oid, chat_id, res['result']['message_id'], drv['id']))
                        conn.commit()
        elif action == 'done':
            oid = int(parts[1])
            o = db.order_full(conn, oid)
            if not o or o['driver_id'] != drv['id'] or o['status'] != 'claimed':
                _answer(cb['id'], token, 'هذا الطلب لم يعد باسمك.')
                return
            db.mark_delivered(conn, oid)
            _answer(cb['id'], token, '✅ تم تسجيل التعبئة، شكرًا!')
            update_order_messages(conn, oid)
        elif action == 'cancel':
            oid = int(parts[1])
            o = db.order_full(conn, oid)
            if not o or o['driver_id'] != drv['id'] or o['status'] != 'claimed':
                _answer(cb['id'], token, 'هذا الطلب لم يعد باسمك.')
                return
            rows = [[{'text': reason, 'callback_data': f"reason:{oid}:{i}"}]
                    for i, reason in enumerate(CANCEL_REASONS)]
            rows.append([{'text': '↩ رجوع', 'callback_data': f"back:{oid}"}])
            api('editMessageReplyMarkup', token, chat_id=chat_id, message_id=message_id,
                reply_markup={'inline_keyboard': rows})
            _answer(cb['id'], token, 'اختر سبب الإلغاء')
        elif action == 'reason':
            oid, idx = int(parts[1]), int(parts[2])
            reason = CANCEL_REASONS[idx] if 0 <= idx < len(CANCEL_REASONS) else 'سبب آخر'
            cur = conn.execute(
                "UPDATE orders SET status='canceled', cancel_reason=? "
                "WHERE id=? AND driver_id=? AND status='claimed'", (reason, oid, drv['id']))
            conn.commit()
            if cur.rowcount:
                _answer(cb['id'], token, '❌ أُلغي الطلب وأُبلغت الإدارة')
            else:
                _answer(cb['id'], token, 'هذا الطلب لم يعد باسمك.')
            update_order_messages(conn, oid)
        elif action == 'back':
            oid = int(parts[1])
            o = db.order_full(conn, oid)
            if o:
                api('editMessageReplyMarkup', token, chat_id=chat_id, message_id=message_id,
                    reply_markup=order_keyboard(o, drv['id']))
            _answer(cb['id'], token)
        elif action == 'pp':
            oid = int(parts[1])
            cur = conn.execute(
                "UPDATE orders SET status='postponed' "
                "WHERE id=? AND driver_id=? AND status='claimed'", (oid, drv['id']))
            conn.commit()
            if cur.rowcount:
                _answer(cb['id'], token, '⏳ أُجّل الطلب')
            else:
                _answer(cb['id'], token, 'هذا الطلب لم يعد باسمك.')
            update_order_messages(conn, oid)
        elif action == 'loc':
            oid = int(parts[1])
            o = db.order_full(conn, oid)
            if not o or o['driver_id'] != drv['id'] or o['status'] != 'claimed':
                _answer(cb['id'], token, 'هذا الطلب لم يعد باسمك.')
                return
            conn.execute("UPDATE drivers SET pending_loc_order=? WHERE id=?",
                         (oid, drv['id']))
            conn.commit()
            api('sendMessage', token, chat_id=chat_id,
                text=(f"📍 تحديث موقع المشترك «{o['customer_name']}» — طلب #{oid}\n"
                      "وأنت واقف عند المنزل اضغط الزر بالأسفل، وسيُحفظ موقعك "
                      "كموقع المنزل في النظام."),
                reply_markup={'keyboard': [
                    [{'text': '📍 إرسال موقعي الحالي', 'request_location': True}],
                    [{'text': 'إلغاء'}]],
                    'resize_keyboard': True, 'one_time_keyboard': True})
            _answer(cb['id'], token)
        else:
            _answer(cb['id'], token)
    finally:
        conn.close()


# ---------- رسائل البوت ----------

def _handle_location(conn, drv, msg, token):
    chat_id = msg['chat']['id']
    oid = drv['pending_loc_order']
    o = db.order_full(conn, oid) if oid else None
    if not o:
        conn.execute("UPDATE drivers SET pending_loc_order=NULL WHERE id=?", (drv['id'],))
        conn.commit()
        api('sendMessage', token, chat_id=chat_id, text='لا يوجد طلب بانتظار تحديث موقع.',
            reply_markup={'remove_keyboard': True})
        return
    lat = msg['location']['latitude']
    lng = msg['location']['longitude']
    maps_url = f"https://maps.google.com/?q={lat:.6f},{lng:.6f}"
    conn.execute("UPDATE customers SET maps_url=? WHERE id=?", (maps_url, o['customer_id']))
    conn.execute("UPDATE drivers SET pending_loc_order=NULL WHERE id=?", (drv['id'],))
    conn.commit()
    api('sendMessage', token, chat_id=chat_id,
        text=f"✅ تم تحديث موقع المشترك «{o['customer_name']}» وحُفظ في النظام.",
        reply_markup={'remove_keyboard': True})
    update_order_messages(conn, oid)


def handle_message(msg, token):
    chat_id = msg['chat']['id']
    text = (msg.get('text') or '').strip()
    conn = db.get_conn()
    try:
        drv, personal = driver_for(conn, token, chat_id)

        # موقع مُرسل من المندوب → تحديث موقع المشترك تلقائيًا
        if msg.get('location'):
            if drv and drv['telegram_chat_id'] == chat_id and drv['pending_loc_order']:
                _handle_location(conn, drv, msg, token)
            return
        if not text:
            return

        # إلغاء طلب الموقع
        if text == 'إلغاء' and drv and drv['pending_loc_order']:
            conn.execute("UPDATE drivers SET pending_loc_order=NULL WHERE id=?", (drv['id'],))
            conn.commit()
            api('sendMessage', token, chat_id=chat_id, text='أُلغي تحديث الموقع.',
                reply_markup={'remove_keyboard': True})
            return

        linked = drv and drv['telegram_chat_id'] == chat_id
        if text.startswith('/start'):
            if linked:
                api('sendMessage', token, chat_id=chat_id,
                    text=f"أهلًا {drv['name']} 👋\nأرسل «الطلبات» لعرض الطلبات المفتوحة.")
            else:
                api('sendMessage', token, chat_id=chat_id,
                    text="أهلًا بك 👋\nأرسل رمز الربط الذي أعطتك إياه الإدارة لتسجيلك كمندوب.")
            return

        if not linked:
            code = text.upper()
            if personal:
                # البوت الخاص: الرمز يجب أن يطابق رمز صاحب هذا البوت تحديدًا
                if drv['telegram_chat_id'] and drv['telegram_chat_id'] != chat_id:
                    api('sendMessage', token, chat_id=chat_id,
                        text='هذا البوت مخصص لمندوب آخر.')
                    return
                if code == (drv['link_code'] or '').upper():
                    conn.execute("UPDATE drivers SET telegram_chat_id=? WHERE id=?",
                                 (chat_id, drv['id']))
                    conn.commit()
                    api('sendMessage', token, chat_id=chat_id,
                        text=f"✅ تم ربطك بنجاح يا {drv['name']}!\n"
                             "ستصلك الطلبات هنا، وأرسل «الطلبات» في أي وقت لعرض المفتوح منها.")
                else:
                    api('sendMessage', token, chat_id=chat_id,
                        text='الرمز غير صحيح. اطلب رمز الربط من الإدارة ثم أرسله هنا.')
                return
            match = conn.execute(
                "SELECT * FROM drivers WHERE link_code=? AND telegram_chat_id IS NULL "
                "AND (bot_token IS NULL OR bot_token='')", (code,)).fetchone()
            if match:
                conn.execute("UPDATE drivers SET telegram_chat_id=? WHERE id=?",
                             (chat_id, match['id']))
                conn.commit()
                api('sendMessage', token, chat_id=chat_id,
                    text=f"✅ تم ربطك بنجاح يا {match['name']}!\n"
                         "ستصلك الطلبات الجديدة هنا، وأرسل «الطلبات» في أي وقت لعرض المفتوح منها.")
            else:
                api('sendMessage', token, chat_id=chat_id,
                    text='الرمز غير صحيح. اطلب رمز الربط من الإدارة ثم أرسله هنا.')
            return

        if not drv['active']:
            api('sendMessage', token, chat_id=chat_id, text='حسابك موقوف حاليًا — راجع الإدارة.')
            return

        if text in ('/orders', 'الطلبات', 'طلبات'):
            body, kb = open_orders_message(conn)
            api('sendMessage', token, chat_id=chat_id, text=body, reply_markup=kb)
        elif text in ('طلباتي', '/my'):
            rows = conn.execute(
                db.ORDER_FULL_SQL + " WHERE o.driver_id=? AND o.status='claimed' ORDER BY o.id",
                (drv['id'],)).fetchall()
            if not rows:
                api('sendMessage', token, chat_id=chat_id, text='لا توجد طلبات باسمك حاليًا.')
            else:
                for o in rows:
                    api('sendMessage', token, chat_id=chat_id, text=order_text(o),
                        reply_markup=order_keyboard(o, drv['id']))
        else:
            api('sendMessage', token, chat_id=chat_id,
                text='الأوامر المتاحة:\n«الطلبات» — عرض الطلبات المفتوحة\n«طلباتي» — الطلبات التي باسمك')
    finally:
        conn.close()


# ---------- إدارة عدة بوتات معًا ----------

def all_tokens():
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT DISTINCT bot_token FROM drivers "
        "WHERE bot_token IS NOT NULL AND bot_token != ''").fetchall()
    conn.close()
    tokens = {r[0].strip() for r in rows if r[0] and r[0].strip()}
    g = global_token()
    if g:
        tokens.add(g)
    return tokens


def poll_token(token, state):
    while not state['stop']:
        res = api('getUpdates', token, offset=state['offset'], timeout=50,
                  allowed_updates=['message', 'callback_query'])
        if not res or not res.get('ok'):
            time.sleep(5)
            continue
        for update in res.get('result', []):
            state['offset'] = update['update_id'] + 1
            try:
                if 'callback_query' in update:
                    handle_callback(update['callback_query'], token)
                elif 'message' in update:
                    handle_message(update['message'], token)
            except Exception:
                # لا نوقف البوت بسبب تحديث واحد معطوب
                pass


def manager_loop():
    """يشغّل بوتًا لكل رمز (بوتات المندوبين + البوت العام) ويتابع الإضافات فورًا."""
    while True:
        try:
            tokens = all_tokens()
        except Exception:
            tokens = set()
        for t in tokens - set(_pollers):
            state = {'stop': False, 'offset': 0}
            _pollers[t] = state
            threading.Thread(target=poll_token, args=(t, state),
                             daemon=True, name=f'bot-{t[:8]}').start()
        for t in set(_pollers) - tokens:
            _pollers.pop(t)['stop'] = True
        time.sleep(10)


def start():
    thread = threading.Thread(target=manager_loop, daemon=True, name='bot-manager')
    thread.start()
    return thread
