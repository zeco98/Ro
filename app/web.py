# -*- coding: utf-8 -*-
"""لوحة الإدارة — تطبيق ويب محلي يعمل على حاسبة المحل."""
from datetime import date

from flask import (Flask, flash, redirect, render_template, request,
                   session, url_for)

from . import bot, db


def create_app():
    app = Flask(__name__)
    app.secret_key = db.get_setting('secret_key')

    @app.context_processor
    def inject_globals():
        return {
            'factory_name': db.get_setting('factory_name', 'معمل ماء RO'),
            'TANK_STATUS': db.TANK_STATUS,
            'ORDER_STATUS': db.ORDER_STATUS,
        }

    @app.before_request
    def require_login():
        if request.endpoint in ('login', 'static'):
            return None
        if not session.get('auth'):
            return redirect(url_for('login'))
        return None

    # ---------- الدخول ----------
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            if request.form.get('password') == db.get_setting('admin_password', 'admin'):
                session['auth'] = True
                return redirect(url_for('dashboard'))
            flash('كلمة المرور غير صحيحة', 'error')
        return render_template('login.html')

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    # ---------- لوحة التحكم ----------
    @app.route('/')
    def dashboard():
        conn = db.get_conn()
        today = date.today().isoformat()
        month = today[:7]

        def one(sql, *params):
            return conn.execute(sql, params).fetchone()[0]

        stats = {
            'orders_today': one("SELECT COUNT(*) FROM orders WHERE substr(created_at,1,10)=?", today),
            'delivered_today': one("SELECT COUNT(*) FROM orders WHERE status='delivered' AND substr(delivered_at,1,10)=?", today),
            'canceled_today': one("SELECT COUNT(*) FROM orders WHERE status='canceled' AND substr(created_at,1,10)=?", today),
            'open_now': one("SELECT COUNT(*) FROM orders WHERE status IN ('open','claimed','postponed')"),
            'new_customers_month': one("SELECT COUNT(*) FROM customers WHERE substr(created_at,1,7)=?", month),
            'tanks_out': one("SELECT COUNT(*) FROM tanks WHERE status='with_customer'"),
        }
        customers = db.customers_with_cycle(conn)
        stats['late'] = sum(1 for _, info in customers if info['key'] == 'late')
        stats['soon'] = sum(1 for _, info in customers if info['key'] == 'soon')
        late_list = sorted((pair for pair in customers if pair[1]['key'] == 'late'),
                           key=lambda p: -p[1]['late'])[:10]
        today_orders = conn.execute(
            db.ORDER_FULL_SQL + " WHERE substr(o.created_at,1,10)=? ORDER BY o.id DESC",
            (today,)).fetchall()
        conn.close()
        return render_template('dashboard.html', stats=stats, late_list=late_list,
                               today_orders=today_orders)

    # ---------- المشتركون ----------
    @app.route('/customers')
    def customers():
        q = request.args.get('q', '').strip()
        conn = db.get_conn()
        items = db.customers_with_cycle(conn, q or None)
        conn.close()
        return render_template('customers.html', items=items, q=q)

    def _customer_from_form():
        f = request.form
        return {k: f.get(k, '').strip() or None for k in
                ('sub_no', 'name', 'phone', 'governorate', 'area', 'street',
                 'landmark', 'maps_url', 'notes', 'sub_start_date')}

    @app.route('/customers/new', methods=['GET', 'POST'])
    def customer_new():
        if request.method == 'POST':
            data = _customer_from_form()
            if not data['name']:
                flash('اسم المشترك مطلوب', 'error')
            else:
                conn = db.get_conn()
                conn.execute(
                    "INSERT INTO customers(sub_no,name,phone,governorate,area,street,landmark,"
                    "maps_url,notes,sub_start_date,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (data['sub_no'], data['name'], data['phone'], data['governorate'],
                     data['area'], data['street'], data['landmark'], data['maps_url'],
                     data['notes'], data['sub_start_date'] or date.today().isoformat(),
                     db.now_str()))
                conn.commit()
                conn.close()
                flash('تمت إضافة المشترك', 'ok')
                return redirect(url_for('customers'))
        return render_template('customer_form.html', c=None)

    @app.route('/customers/<int:cid>/edit', methods=['GET', 'POST'])
    def customer_edit(cid):
        conn = db.get_conn()
        c = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        if not c:
            conn.close()
            flash('المشترك غير موجود', 'error')
            return redirect(url_for('customers'))
        if request.method == 'POST':
            data = _customer_from_form()
            data['sub_status'] = request.form.get('sub_status', 'active')
            conn.execute(
                "UPDATE customers SET sub_no=?,name=?,phone=?,governorate=?,area=?,street=?,"
                "landmark=?,maps_url=?,notes=?,sub_start_date=?,sub_status=? WHERE id=?",
                (data['sub_no'], data['name'], data['phone'], data['governorate'],
                 data['area'], data['street'], data['landmark'], data['maps_url'],
                 data['notes'], data['sub_start_date'], data['sub_status'], cid))
            conn.commit()
            conn.close()
            flash('تم حفظ التعديلات', 'ok')
            return redirect(url_for('customers'))
        tanks = conn.execute("SELECT * FROM tanks WHERE customer_id=?", (cid,)).fetchall()
        conn.close()
        return render_template('customer_form.html', c=c, tanks=tanks)

    @app.route('/customers/<int:cid>/delete', methods=['POST'])
    def customer_delete(cid):
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM customers WHERE id=?", (cid,))
            conn.commit()
            flash('تم حذف المشترك', 'ok')
        except Exception:
            flash('لا يمكن حذف مشترك له طلبات مسجلة — يمكن إيقاف اشتراكه بدلًا من ذلك', 'error')
        finally:
            conn.close()
        return redirect(url_for('customers'))

    # ---------- الخزانات ----------
    @app.route('/tanks')
    def tanks():
        q = request.args.get('q', '').strip()
        conn = db.get_conn()
        sql = ("SELECT t.*, c.name AS customer_name FROM tanks t "
               "LEFT JOIN customers c ON c.id=t.customer_id")
        params = []
        if q:
            sql += " WHERE t.number LIKE ? OR c.name LIKE ?"
            params = [f'%{q}%'] * 2
        sql += " ORDER BY t.id DESC"
        items = conn.execute(sql, params).fetchall()
        conn.close()
        return render_template('tanks.html', items=items, q=q)

    def _tank_form_data():
        f = request.form
        return {
            'number': f.get('number', '').strip(),
            'capacity': int(f.get('capacity') or 500),
            'status': f.get('status', 'with_customer'),
            'customer_id': int(f['customer_id']) if f.get('customer_id') else None,
            'delivered_date': f.get('delivered_date') or None,
            'maintenance_date': f.get('maintenance_date') or None,
            'damage_reason': f.get('damage_reason', '').strip() or None,
            'notes': f.get('notes', '').strip() or None,
        }

    @app.route('/tanks/new', methods=['GET', 'POST'])
    def tank_new():
        conn = db.get_conn()
        if request.method == 'POST':
            d = _tank_form_data()
            if not d['number']:
                flash('رقم الخزان مطلوب', 'error')
            else:
                try:
                    conn.execute(
                        "INSERT INTO tanks(number,capacity,status,customer_id,delivered_date,"
                        "maintenance_date,damage_reason,notes) VALUES(?,?,?,?,?,?,?,?)",
                        (d['number'], d['capacity'], d['status'], d['customer_id'],
                         d['delivered_date'], d['maintenance_date'], d['damage_reason'],
                         d['notes']))
                    conn.commit()
                    conn.close()
                    flash('تمت إضافة الخزان', 'ok')
                    return redirect(url_for('tanks'))
                except Exception:
                    flash('رقم الخزان مستخدم مسبقًا', 'error')
        customers_all = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        conn.close()
        return render_template('tank_form.html', t=None, customers=customers_all)

    @app.route('/tanks/<int:tid>/edit', methods=['GET', 'POST'])
    def tank_edit(tid):
        conn = db.get_conn()
        t = conn.execute("SELECT * FROM tanks WHERE id=?", (tid,)).fetchone()
        if not t:
            conn.close()
            flash('الخزان غير موجود', 'error')
            return redirect(url_for('tanks'))
        if request.method == 'POST':
            d = _tank_form_data()
            try:
                conn.execute(
                    "UPDATE tanks SET number=?,capacity=?,status=?,customer_id=?,"
                    "delivered_date=?,maintenance_date=?,damage_reason=?,notes=? WHERE id=?",
                    (d['number'], d['capacity'], d['status'], d['customer_id'],
                     d['delivered_date'], d['maintenance_date'], d['damage_reason'],
                     d['notes'], tid))
                conn.commit()
                conn.close()
                flash('تم حفظ التعديلات', 'ok')
                return redirect(url_for('tanks'))
            except Exception:
                flash('رقم الخزان مستخدم مسبقًا', 'error')
        customers_all = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        conn.close()
        return render_template('tank_form.html', t=t, customers=customers_all)

    # ---------- الطلبات ----------
    @app.route('/orders')
    def orders():
        status = request.args.get('status', '').strip()
        conn = db.get_conn()
        sql = db.ORDER_FULL_SQL
        params = []
        if status:
            sql += " WHERE o.status=?"
            params = [status]
        sql += " ORDER BY o.id DESC LIMIT 300"
        items = conn.execute(sql, params).fetchall()
        conn.close()
        return render_template('orders.html', items=items, status=status)

    @app.route('/orders/new')
    def order_new():
        q = request.args.get('q', '').strip()
        results = []
        if q:
            conn = db.get_conn()
            pairs = db.customers_with_cycle(conn, q)
            for c, info in pairs:
                c_tanks = conn.execute(
                    "SELECT * FROM tanks WHERE customer_id=?", (c['id'],)).fetchall()
                results.append((c, info, c_tanks))
            conn.close()
        return render_template('order_new.html', q=q, results=results)

    @app.route('/orders/create', methods=['POST'])
    def order_create():
        customer_id = int(request.form['customer_id'])
        tank_id = int(request.form['tank_id']) if request.form.get('tank_id') else None
        notes = request.form.get('notes', '').strip() or None
        conn = db.get_conn()
        cur = conn.execute(
            "INSERT INTO orders(customer_id, tank_id, notes, status, created_at) "
            "VALUES(?,?,?,'open',?)", (customer_id, tank_id, notes, db.now_str()))
        conn.commit()
        oid = cur.lastrowid
        conn.close()
        sent = bot.broadcast_order(oid)
        if sent:
            flash(f'أُنشئ الطلب #{oid} وأُرسل إلى {sent} مندوب', 'ok')
        else:
            flash(f'أُنشئ الطلب #{oid} — لم يُرسل لأي مندوب '
                  '(تأكد من ضبط رمز البوت وربط المندوبين)', 'error')
        return redirect(url_for('orders'))

    @app.route('/orders/<int:oid>/resend', methods=['POST'])
    def order_resend(oid):
        conn = db.get_conn()
        conn.execute(
            "UPDATE orders SET status='open', driver_id=NULL, claimed_at=NULL, "
            "cancel_reason=NULL WHERE id=? AND status IN ('open','postponed','canceled')",
            (oid,))
        conn.execute("DELETE FROM order_messages WHERE order_id=?", (oid,))
        conn.commit()
        conn.close()
        sent = bot.broadcast_order(oid)
        flash(f'أُعيد إرسال الطلب #{oid} إلى {sent} مندوب' if sent
              else 'لم يُرسل لأي مندوب — تأكد من إعدادات البوت', 'ok' if sent else 'error')
        return redirect(url_for('orders'))

    @app.route('/orders/<int:oid>/deliver', methods=['POST'])
    def order_deliver(oid):
        conn = db.get_conn()
        ok = db.mark_delivered(conn, oid)
        conn.close()
        if ok:
            bot.notify_order_change(oid)
            flash(f'سُجل تسليم الطلب #{oid}', 'ok')
        else:
            flash('لا يمكن تسليم هذا الطلب', 'error')
        return redirect(url_for('orders'))

    @app.route('/orders/<int:oid>/cancel', methods=['POST'])
    def order_cancel(oid):
        reason = request.form.get('reason', '').strip() or 'أُلغي من الإدارة'
        conn = db.get_conn()
        conn.execute(
            "UPDATE orders SET status='canceled', cancel_reason=? "
            "WHERE id=? AND status NOT IN ('delivered','canceled')", (reason, oid))
        conn.commit()
        conn.close()
        bot.notify_order_change(oid)
        flash(f'أُلغي الطلب #{oid}', 'ok')
        return redirect(url_for('orders'))

    # ---------- المندوبون ----------
    @app.route('/drivers')
    def drivers():
        conn = db.get_conn()
        today = date.today().isoformat()
        items = conn.execute("""
            SELECT d.*,
              (SELECT COUNT(*) FROM orders o WHERE o.driver_id=d.id
                 AND substr(o.created_at,1,10)=?) AS today_orders,
              (SELECT COUNT(*) FROM orders o WHERE o.driver_id=d.id
                 AND o.status='delivered' AND substr(o.delivered_at,1,10)=?) AS today_done,
              (SELECT COUNT(*) FROM orders o WHERE o.driver_id=d.id
                 AND o.status='delivered') AS total_done,
              (SELECT COUNT(*) FROM orders o WHERE o.driver_id=d.id
                 AND o.status='canceled') AS total_canceled
            FROM drivers d ORDER BY d.id
        """, (today, today)).fetchall()
        conn.close()
        return render_template('drivers.html', items=items)

    @app.route('/drivers/new', methods=['GET', 'POST'])
    def driver_new():
        if request.method == 'POST':
            f = request.form
            name = f.get('name', '').strip()
            if not name:
                flash('اسم المندوب مطلوب', 'error')
            else:
                conn = db.get_conn()
                conn.execute(
                    "INSERT INTO drivers(name, phone, car, areas, link_code) VALUES(?,?,?,?,?)",
                    (name, f.get('phone', '').strip() or None,
                     f.get('car', '').strip() or None,
                     f.get('areas', '').strip() or None, db.new_link_code()))
                conn.commit()
                conn.close()
                flash('أُضيف المندوب — أعطه رمز الربط ليرسله إلى البوت', 'ok')
                return redirect(url_for('drivers'))
        return render_template('driver_form.html', d=None)

    @app.route('/drivers/<int:did>/edit', methods=['GET', 'POST'])
    def driver_edit(did):
        conn = db.get_conn()
        d = conn.execute("SELECT * FROM drivers WHERE id=?", (did,)).fetchone()
        if not d:
            conn.close()
            flash('المندوب غير موجود', 'error')
            return redirect(url_for('drivers'))
        if request.method == 'POST':
            f = request.form
            conn.execute(
                "UPDATE drivers SET name=?, phone=?, car=?, areas=?, active=? WHERE id=?",
                (f.get('name', '').strip(), f.get('phone', '').strip() or None,
                 f.get('car', '').strip() or None, f.get('areas', '').strip() or None,
                 1 if f.get('active') else 0, did))
            conn.commit()
            conn.close()
            flash('تم حفظ التعديلات', 'ok')
            return redirect(url_for('drivers'))
        conn.close()
        return render_template('driver_form.html', d=d)

    @app.route('/drivers/<int:did>/newcode', methods=['POST'])
    def driver_newcode(did):
        conn = db.get_conn()
        conn.execute("UPDATE drivers SET link_code=?, telegram_chat_id=NULL WHERE id=?",
                     (db.new_link_code(), did))
        conn.commit()
        conn.close()
        flash('أُنشئ رمز ربط جديد وأُلغي الربط السابق', 'ok')
        return redirect(url_for('drivers'))

    # ---------- المتأخرون ----------
    @app.route('/late')
    def late():
        conn = db.get_conn()
        pairs = db.customers_with_cycle(conn)
        conn.close()
        late_pairs = sorted((p for p in pairs if p[1]['key'] == 'late'),
                            key=lambda p: -p[1]['late'])
        soon_pairs = sorted((p for p in pairs if p[1]['key'] == 'soon'),
                            key=lambda p: p[1]['remaining'])
        return render_template('late.html', late_pairs=late_pairs, soon_pairs=soon_pairs)

    # ---------- التقارير ----------
    @app.route('/reports')
    def reports():
        conn = db.get_conn()
        monthly = conn.execute(
            "SELECT substr(delivered_at,1,7) AS m, COUNT(*) AS n FROM orders "
            "WHERE status='delivered' GROUP BY m ORDER BY m DESC LIMIT 12").fetchall()
        areas = conn.execute(
            "SELECT c.area AS area, COUNT(*) AS n FROM orders o "
            "JOIN customers c ON c.id=o.customer_id WHERE o.status='delivered' "
            "GROUP BY c.area ORDER BY n DESC LIMIT 15").fetchall()
        driver_stats = conn.execute("""
            SELECT d.name,
              SUM(CASE WHEN o.status='delivered' THEN 1 ELSE 0 END) AS delivered,
              SUM(CASE WHEN o.status='canceled' THEN 1 ELSE 0 END) AS canceled
            FROM drivers d LEFT JOIN orders o ON o.driver_id=d.id
            GROUP BY d.id ORDER BY delivered DESC
        """).fetchall()
        bad_tanks = conn.execute(
            "SELECT t.*, c.name AS customer_name FROM tanks t "
            "LEFT JOIN customers c ON c.id=t.customer_id "
            "WHERE t.status IN ('damaged','lost','maintenance') ORDER BY t.id DESC").fetchall()
        cancel_reasons = conn.execute(
            "SELECT cancel_reason AS reason, COUNT(*) AS n FROM orders "
            "WHERE status='canceled' GROUP BY cancel_reason ORDER BY n DESC").fetchall()
        conn.close()
        return render_template('reports.html', monthly=monthly, areas=areas,
                               driver_stats=driver_stats, bad_tanks=bad_tanks,
                               cancel_reasons=cancel_reasons)

    # ---------- الإعدادات ----------
    @app.route('/settings', methods=['GET', 'POST'])
    def settings():
        keys = ('factory_name', 'cycle_days', 'warn_days', 'bot_token')
        if request.method == 'POST':
            for key in keys:
                if key in request.form:
                    db.set_setting(key, request.form[key].strip())
            new_pass = request.form.get('admin_password', '').strip()
            if new_pass:
                db.set_setting('admin_password', new_pass)
            flash('تم حفظ الإعدادات', 'ok')
            return redirect(url_for('settings'))
        values = {key: db.get_setting(key) for key in keys}
        return render_template('settings.html', values=values)

    return app
