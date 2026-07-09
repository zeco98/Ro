# -*- coding: utf-8 -*-
"""طبقة قاعدة البيانات — SQLite ملف واحد بجانب المشروع (data.db)."""
import sqlite3
import secrets
import string
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sub_no TEXT,
  name TEXT NOT NULL,
  phone TEXT,
  governorate TEXT,
  area TEXT,
  street TEXT,
  landmark TEXT,
  maps_url TEXT,
  notes TEXT,
  sub_start_date TEXT,
  sub_status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tanks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  number TEXT NOT NULL UNIQUE,
  capacity INTEGER NOT NULL DEFAULT 500,
  status TEXT NOT NULL DEFAULT 'with_customer',
  customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
  delivered_date TEXT,
  last_fill_date TEXT,
  fill_count INTEGER NOT NULL DEFAULT 0,
  maintenance_date TEXT,
  damage_reason TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS drivers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT,
  car TEXT,
  areas TEXT,
  telegram_chat_id INTEGER,
  link_code TEXT,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  tank_id INTEGER REFERENCES tanks(id) ON DELETE SET NULL,
  driver_id INTEGER REFERENCES drivers(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  claimed_at TEXT,
  delivered_at TEXT,
  cancel_reason TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS order_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  chat_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
"""

TANK_STATUS = {
    'available': 'في المستودع',
    'with_customer': 'عند الزبون',
    'returned': 'مسترجع',
    'maintenance': 'في الصيانة',
    'damaged': 'تالف',
    'lost': 'مفقود',
}

ORDER_STATUS = {
    'open': 'جديد',
    'claimed': 'بالطريق',
    'delivered': 'تم التسليم',
    'canceled': 'ملغي',
    'postponed': 'مؤجل',
}

DEFAULT_SETTINGS = {
    'factory_name': 'معمل ماء RO',
    'cycle_days': '30',
    'warn_days': '5',
    'admin_password': 'admin',
    'bot_token': '',
}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init():
    conn = get_conn()
    conn.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
    if not conn.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone():
        conn.execute("INSERT INTO settings(key, value) VALUES('secret_key', ?)",
                     (secrets.token_hex(32),))
    conn.commit()
    conn.close()


def get_setting(key, default=''):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row['value'] if row and row['value'] is not None else default


def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
    conn.close()


def new_link_code():
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(6))


def now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M')


def parse_day(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def cycle_info(row):
    """حالة اشتراك الزبون اعتمادًا على آخر تعبئة (أو بداية الاشتراك إن لم توجد)."""
    cycle_days = int(get_setting('cycle_days', '30') or 30)
    warn_days = int(get_setting('warn_days', '5') or 5)
    if row['sub_status'] == 'suspended':
        return {'key': 'suspended', 'label': 'موقوف', 'last': None,
                'remaining': None, 'late': 0}
    keys = row.keys()
    candidates = [parse_day(row[k]) for k in ('last_order_fill', 'tank_fill', 'sub_start_date')
                  if k in keys]
    candidates = [d for d in candidates if d]
    if not candidates:
        return {'key': 'none', 'label': 'بلا تعبئة', 'last': None,
                'remaining': None, 'late': 0}
    last = max(candidates)
    days_since = (date.today() - last).days
    remaining = cycle_days - days_since
    if remaining < 0:
        return {'key': 'late', 'label': f'متأخر {-remaining} يوم', 'last': last,
                'remaining': remaining, 'late': -remaining}
    if remaining <= warn_days:
        return {'key': 'soon', 'label': f'سينتهي قريبًا (باقٍ {remaining} يوم)',
                'last': last, 'remaining': remaining, 'late': 0}
    return {'key': 'active', 'label': f'نشط (باقٍ {remaining} يوم)', 'last': last,
            'remaining': remaining, 'late': 0}


CUSTOMER_CYCLE_SQL = """
SELECT c.*,
  (SELECT MAX(substr(o.delivered_at,1,10)) FROM orders o
     WHERE o.customer_id=c.id AND o.status='delivered') AS last_order_fill,
  (SELECT MAX(t.last_fill_date) FROM tanks t WHERE t.customer_id=c.id) AS tank_fill,
  (SELECT GROUP_CONCAT(t.number, '، ') FROM tanks t WHERE t.customer_id=c.id) AS tank_numbers
FROM customers c
"""


def customers_with_cycle(conn, q=None):
    sql = CUSTOMER_CYCLE_SQL
    params = []
    if q:
        like = f'%{q}%'
        sql += (" WHERE c.name LIKE ? OR c.phone LIKE ? OR c.area LIKE ? OR c.sub_no LIKE ?"
                " OR EXISTS (SELECT 1 FROM tanks t WHERE t.customer_id=c.id AND t.number LIKE ?)")
        params = [like] * 5
    sql += " ORDER BY c.id DESC"
    rows = conn.execute(sql, params).fetchall()
    return [(r, cycle_info(r)) for r in rows]


ORDER_FULL_SQL = """
SELECT o.*, c.name AS customer_name, c.phone AS customer_phone, c.area AS area,
       c.street AS street, c.landmark AS landmark, c.maps_url AS maps_url,
       t.number AS tank_number, t.capacity AS capacity,
       d.name AS driver_name, d.telegram_chat_id AS driver_chat_id
FROM orders o
JOIN customers c ON c.id = o.customer_id
LEFT JOIN tanks t ON t.id = o.tank_id
LEFT JOIN drivers d ON d.id = o.driver_id
"""


def order_full(conn, order_id):
    return conn.execute(ORDER_FULL_SQL + " WHERE o.id=?", (order_id,)).fetchone()


def mark_delivered(conn, order_id, when=None):
    """تسليم الطلب وتحديث بيانات الخزان (آخر تعبئة + عداد التعبئات)."""
    when = when or now_str()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order or order['status'] in ('delivered', 'canceled'):
        return False
    conn.execute("UPDATE orders SET status='delivered', delivered_at=? WHERE id=?",
                 (when, order_id))
    if order['tank_id']:
        conn.execute(
            "UPDATE tanks SET last_fill_date=?, fill_count=fill_count+1 WHERE id=?",
            (when[:10], order['tank_id']))
    conn.commit()
    return True
