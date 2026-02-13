"""
БД ParkingBot — SQLite + WAL
"""
import sqlite3, json, logging, os
from datetime import datetime, timedelta
from contextlib import contextmanager
from config import DATABASE_PATH
from utils import normalize_dt, now_local, calculate_price

logger = logging.getLogger(__name__)
_wal_set = False

@contextmanager
def get_connection():
    global _wal_set
    os.makedirs(os.path.dirname(DATABASE_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    if not _wal_set:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            _wal_set = True
        except: pass
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"DB error: {e}")
        raise
    finally:
        conn.close()

def _log(cursor, action, user_id=None, spot_id=None, booking_id=None, details=None):
    try:
        cursor.execute('INSERT INTO admin_logs (action_type,user_id,spot_id,booking_id,details) VALUES (?,?,?,?,?)',
                       (action, user_id, spot_id, booking_id, details))
    except: pass


def _parse_db_dt(val) -> datetime:
    """Парсит datetime из БД. В БД может быть формат с секундами или без."""
    if isinstance(val, datetime):
        return val
    s = str(val)
    # datetime.fromisoformat понимает 'YYYY-MM-DD HH:MM(:SS)'
    return datetime.fromisoformat(s)

def init_database():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT, full_name TEXT NOT NULL, phone TEXT NOT NULL,
            card_number TEXT DEFAULT '', bank TEXT DEFAULT '',
            license_plate TEXT DEFAULT '', car_brand TEXT DEFAULT '', car_color TEXT DEFAULT '',
            role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
            banned_until TEXT DEFAULT NULL, ban_reason TEXT DEFAULT '',
            balance REAL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        for col, typ in [('license_plate','TEXT DEFAULT ""'),('car_brand','TEXT DEFAULT ""'),
                         ('car_color','TEXT DEFAULT ""'),('banned_until','TEXT DEFAULT NULL'),
                         ('ban_reason','TEXT DEFAULT ""')]:
            try: c.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
            except: pass

        c.execute('''CREATE TABLE IF NOT EXISTS parking_spots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL, spot_number TEXT NOT NULL,
            address TEXT, description TEXT, price_per_hour REAL NOT NULL DEFAULT 0,
            is_available INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (supplier_id) REFERENCES users(id))''')

        c.execute('''CREATE TABLE IF NOT EXISTS spot_availability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spot_id INTEGER NOT NULL,
            start_time TIMESTAMP NOT NULL, end_time TIMESTAMP NOT NULL,
            is_booked INTEGER DEFAULT 0, booked_by INTEGER, booking_id INTEGER,
            FOREIGN KEY (spot_id) REFERENCES parking_spots(id))''')

        c.execute('''CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL, spot_id INTEGER NOT NULL,
            availability_id INTEGER,
            start_time TIMESTAMP NOT NULL, end_time TIMESTAMP NOT NULL,
            total_price REAL NOT NULL,
            status TEXT DEFAULT 'pending', payment_status TEXT DEFAULT 'unpaid',
            reviewed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES users(id))''')
        try: c.execute("ALTER TABLE bookings ADD COLUMN reviewed INTEGER DEFAULT 0")
        except: pass

        c.execute('''CREATE TABLE IF NOT EXISTS spot_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, spot_id INTEGER,
            desired_date DATE, start_time TIME, end_time TIME,
            notify_any INTEGER DEFAULT 1, is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        c.execute('''CREATE TABLE IF NOT EXISTS admin_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, telegram_id INTEGER NOT NULL,
            session_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        c.execute('''CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL, user_id INTEGER, spot_id INTEGER,
            booking_id INTEGER, details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        c.execute('''CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER NOT NULL UNIQUE,
            reviewer_id INTEGER NOT NULL, spot_id INTEGER NOT NULL,
            supplier_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        c.execute('''CREATE TABLE IF NOT EXISTS user_blacklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, blocked_user_id INTEGER NOT NULL,
            reason TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, blocked_user_id))''')

        # Хранилище для подтверждений (если бот перезапустился и FSM-состояние потерялось)
        c.execute('''CREATE TABLE IF NOT EXISTS slot_confirms (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            spot_number TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            price REAL NOT NULL,
            created_at TEXT NOT NULL
        )''')

        for idx in [
            'CREATE INDEX IF NOT EXISTS idx_u_tg ON users(telegram_id)',
            'CREATE INDEX IF NOT EXISTS idx_sp_sup ON parking_spots(supplier_id)',
            'CREATE INDEX IF NOT EXISTS idx_sa_sp ON spot_availability(spot_id)',
            'CREATE INDEX IF NOT EXISTS idx_sa_bk ON spot_availability(is_booked)',
            'CREATE INDEX IF NOT EXISTS idx_bk_cust ON bookings(customer_id)',
            'CREATE INDEX IF NOT EXISTS idx_bk_st ON bookings(status)',
        ]: c.execute(idx)
        logger.info("Database initialized")


# ==================== USERS ====================
def get_user_by_telegram_id(tid):
    with get_connection() as conn:
        r = conn.cursor().execute('SELECT * FROM users WHERE telegram_id=?',(tid,)).fetchone()
        return dict(r) if r else None

def get_user_by_id(uid):
    with get_connection() as conn:
        r = conn.cursor().execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        return dict(r) if r else None

def create_user(telegram_id, username, full_name, phone, card_number='', bank=''):
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('INSERT INTO users (telegram_id,username,full_name,phone,card_number,bank) VALUES (?,?,?,?,?,?)',
                  (telegram_id, username, full_name, phone, card_number, bank))
        uid = c.lastrowid
        _log(c, 'user_registered', user_id=uid, details=json.dumps({'name':full_name,'phone':phone}))
        return uid

def update_user(user_id, **kw):
    ok = ['username','full_name','phone','card_number','bank','role','is_active','balance',
          'license_plate','car_brand','car_color','banned_until','ban_reason']
    u = {k:v for k,v in kw.items() if k in ok}
    if not u: return False
    s = ', '.join(f"{k}=?" for k in u)
    with get_connection() as conn:
        return conn.cursor().execute(f'UPDATE users SET {s} WHERE id=?', list(u.values())+[user_id]).rowcount > 0

def user_has_car_info(u): return bool(u.get('license_plate') and u.get('car_brand') and u.get('car_color'))
def user_has_card_info(u): return bool(u.get('card_number') and u.get('bank'))

def is_user_banned(u):
    if not u.get('is_active'):
        bu = u.get('banned_until')
        if bu:
            try:
                until = datetime.fromisoformat(bu)
                if until > datetime.now():
                    return True, u.get('ban_reason',''), bu
                else:
                    update_user(u['id'], is_active=1, banned_until=None, ban_reason='')
                    return False, '', None
            except: pass
        return True, u.get('ban_reason',''), None
    return False, '', None

def ban_user(user_id, duration_hours=None, reason=''):
    bu = None
    if duration_hours:
        bu = (datetime.now() + timedelta(hours=duration_hours)).strftime("%Y-%m-%d %H:%M:%S")
    return update_user(user_id, is_active=0, banned_until=bu, ban_reason=reason)

def unban_user(user_id):
    return update_user(user_id, is_active=1, banned_until=None, ban_reason='')

def get_all_users(limit=50, offset=0):
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?',(limit,offset)).fetchall()]
def get_active_users():
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('SELECT * FROM users WHERE is_active=1').fetchall()]
def get_users_count():
    with get_connection() as conn: return conn.cursor().execute('SELECT COUNT(*) FROM users').fetchone()[0]

def search_users(query: str, limit: int = 50, offset: int = 0):
    """Поиск пользователей по имени, телефону, username или telegram_id."""
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    with get_connection() as conn:
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT * FROM users
            WHERE full_name LIKE ?
               OR phone LIKE ?
               OR username LIKE ?
               OR CAST(telegram_id AS TEXT) LIKE ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (like, like, like, like, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

def search_users_count(query: str) -> int:
    q = (query or "").strip()
    if not q:
        return 0
    like = f"%{q}%"
    with get_connection() as conn:
        cur = conn.cursor()
        return int(
            cur.execute(
                """
                SELECT COUNT(*) FROM users
                WHERE full_name LIKE ?
                   OR phone LIKE ?
                   OR username LIKE ?
                   OR CAST(telegram_id AS TEXT) LIKE ?
                """,
                (like, like, like, like),
            ).fetchone()[0]
        )
def get_admins():
    with get_connection() as conn: return [dict(r) for r in conn.cursor().execute("SELECT * FROM users WHERE role='admin'").fetchall()]
def set_user_role(uid, role): return update_user(uid, role=role)
def block_user(uid): return update_user(uid, is_active=0)
def unblock_user(uid): return unban_user(uid)


# ==================== SPOTS ====================
def create_parking_spot(supplier_id, spot_number, price_per_hour=0, address=None, description=None):
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('INSERT INTO parking_spots (supplier_id,spot_number,address,description,price_per_hour) VALUES (?,?,?,?,?)',
                  (supplier_id, spot_number, address, description, price_per_hour))
        sid = c.lastrowid
        _log(c, 'spot_added', spot_id=sid, user_id=supplier_id)
        return sid

def get_or_create_spot(supplier_id, spot_number, address=None):
    """Находит существующее место по номеру у поставщика или создаёт новое.
    Если address передан и у существующего места адрес пустой — обновляет.
    """
    with get_connection() as conn:
        c = conn.cursor()
        r = c.execute('SELECT id, address FROM parking_spots WHERE supplier_id=? AND spot_number=? AND is_available=1',
                      (supplier_id, spot_number)).fetchone()
        if r:
            sid = r['id'] if isinstance(r, dict) else r[0]
            cur_addr = r['address'] if isinstance(r, dict) else r[1]
            if address and (cur_addr is None or str(cur_addr).strip() == ''):
                c.execute("UPDATE parking_spots SET address=? WHERE id=?", (address, sid))
            return sid
        c.execute('INSERT INTO parking_spots (supplier_id,spot_number,address,price_per_hour) VALUES (?,?,?,0)',
                  (supplier_id, spot_number, address))
        return c.lastrowid

def create_spot_availability(spot_id, start_time, end_time):
    start_time = normalize_dt(start_time)
    end_time = normalize_dt(end_time)
    if end_time <= start_time:
        raise ValueError("Invalid interval")
    if start_time < now_local():
        raise ValueError("Start time in past")
    with get_connection() as conn:
        return conn.cursor().execute('INSERT INTO spot_availability (spot_id,start_time,end_time) VALUES (?,?,?)',
            (spot_id, start_time.strftime("%Y-%m-%d %H:%M:%S"), end_time.strftime("%Y-%m-%d %H:%M:%S"))).lastrowid


def check_slot_overlap(spot_id, start_time, end_time, exclude_slot_id=None):
    """Проверяет пересечение с существующими слотами. True = есть пересечение."""
    with get_connection() as conn:
        # Не используем SQLite datetime('now','localtime') — на хостинге TZ может быть UTC.
        # Сравниваем относительно нашей локальной TZ (now_local) для консистентной логики.
        q = '''SELECT COUNT(*) FROM spot_availability
               WHERE spot_id=? AND start_time < ? AND end_time > ?
               AND end_time > ?'''
        now_str = now_local().strftime("%Y-%m-%d %H:%M:%S")
        p = [spot_id, end_time.strftime("%Y-%m-%d %H:%M:%S"), start_time.strftime("%Y-%m-%d %H:%M:%S"), now_str]
        if exclude_slot_id:
            q += ' AND id != ?'; p.append(exclude_slot_id)
        return conn.cursor().execute(q, p).fetchone()[0] > 0

def update_slot_times(slot_id, start_time, end_time):
    """Обновляет время свободного слота."""
    with get_connection() as conn:
        return conn.cursor().execute(
            'UPDATE spot_availability SET start_time=?, end_time=? WHERE id=? AND is_booked=0',
            (start_time.strftime("%Y-%m-%d %H:%M:%S"), end_time.strftime("%Y-%m-%d %H:%M:%S"), slot_id)).rowcount > 0

def delete_slot(slot_id):
    """Удаляет свободный слот."""
    with get_connection() as conn:
        return conn.cursor().execute('DELETE FROM spot_availability WHERE id=? AND is_booked=0',(slot_id,)).rowcount > 0

def get_user_spots(uid):
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('SELECT * FROM parking_spots WHERE supplier_id=? AND is_available=1 ORDER BY created_at DESC',(uid,)).fetchall()]
def get_user_spots_count(uid):
    with get_connection() as conn: return conn.cursor().execute('SELECT COUNT(*) FROM parking_spots WHERE supplier_id=? AND is_available=1',(uid,)).fetchone()[0]
def get_spot_by_id(sid):
    with get_connection() as conn:
        r = conn.cursor().execute('SELECT * FROM parking_spots WHERE id=?',(sid,)).fetchone()
        return dict(r) if r else None
def get_all_spots():
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('SELECT ps.*, u.full_name as supplier_name FROM parking_spots ps JOIN users u ON ps.supplier_id=u.id WHERE ps.is_available=1 ORDER BY ps.created_at DESC').fetchall()]


def get_spots_with_free_availabilities(limit: int = 50):
    """Список мест, у которых есть хотя бы один свободный активный слот (end_time > now)."""
    now_str = now_local().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        q = '''SELECT DISTINCT ps.*, u.full_name as supplier_name
               FROM parking_spots ps
               JOIN users u ON ps.supplier_id=u.id
               WHERE ps.is_available=1
                 AND EXISTS (
                     SELECT 1 FROM spot_availability sa
                     WHERE sa.spot_id=ps.id AND sa.is_booked=0 AND sa.end_time > ?
                 )
               ORDER BY ps.created_at DESC
               LIMIT ?'''
        return [dict(r) for r in conn.cursor().execute(q, (now_str, limit)).fetchall()]
def delete_spot(sid):
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM spot_availability WHERE spot_id=? AND is_booked=0',(sid,))
        return c.execute('UPDATE parking_spots SET is_available=0 WHERE id=?',(sid,)).rowcount > 0


# ==================== AVAILABILITY ====================
def get_available_slots(date_str=None, exclude_supplier=None):
    with get_connection() as conn:
        q = '''SELECT sa.*, ps.spot_number, ps.price_per_hour,
               ps.address, ps.description, ps.supplier_id, u.full_name as supplier_name,
               u.card_number, u.bank
               FROM spot_availability sa
               JOIN parking_spots ps ON sa.spot_id = ps.id
               JOIN users u ON ps.supplier_id = u.id
               WHERE sa.is_booked = 0 AND ps.is_available = 1
               AND sa.end_time > ?'''
        now_str = now_local().strftime("%Y-%m-%d %H:%M:%S")
        p = [now_str]
        if date_str:
            q += ' AND DATE(sa.start_time) <= ? AND DATE(sa.end_time) >= ?'
            p.extend([date_str, date_str])
        if exclude_supplier:
            q += ' AND ps.supplier_id != ?'; p.append(exclude_supplier)
        q += ' ORDER BY sa.start_time ASC'
        return [dict(r) for r in conn.cursor().execute(q, p).fetchall()]

def get_availability_by_id(aid):
    with get_connection() as conn:
        r = conn.cursor().execute('''SELECT sa.*, ps.spot_number, ps.price_per_hour,
               ps.address, ps.supplier_id, u.full_name as supplier_name,
               u.card_number, u.bank, u.phone as supplier_phone,
               u.telegram_id as supplier_telegram_id, u.username as supplier_username
               FROM spot_availability sa JOIN parking_spots ps ON sa.spot_id=ps.id
               JOIN users u ON ps.supplier_id=u.id WHERE sa.id=?''',(aid,)).fetchone()
        return dict(r) if r else None

def get_slot_by_id(aid):
    """Получить слот без JOIN."""
    with get_connection() as conn:
        r = conn.cursor().execute('SELECT * FROM spot_availability WHERE id=?',(aid,)).fetchone()
        return dict(r) if r else None

def get_spot_availabilities(sid):
    """Возвращает ТОЛЬКО свободные интервалы для места, которые ещё не закончились."""
    now_str = now_local().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute(
            "SELECT * FROM spot_availability WHERE spot_id=? AND is_booked=0 AND end_time>? ORDER BY start_time ASC",
            (sid, now_str)
        ).fetchall()]


def get_spot_availabilities_all(sid):
    """Для админки: возвращает все интервалы (свободные и забронированные), которые ещё не закончились."""
    now_str = now_local().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute(
            "SELECT * FROM spot_availability WHERE spot_id=? AND end_time>? ORDER BY start_time ASC",
            (sid, now_str)
        ).fetchall()]


def admin_update_availability_interval(slot_id: int, start_dt: datetime, end_dt: datetime) -> bool:
    """Админское редактирование интервала availability.

    Разрешаем менять только слоты, которые не привязаны к брони (booking_id IS NULL).
    Это защищает от поломки оплаченных/активных броней.
    Также проверяем пересечения с другими интервалами этого же места.
    """
    start_dt = normalize_dt(start_dt)
    end_dt = normalize_dt(end_dt)
    if end_dt <= start_dt:
        return False

    with get_connection() as conn:
        c = conn.cursor()
        slot = c.execute(
            "SELECT id, spot_id, booking_id FROM spot_availability WHERE id=?",
            (slot_id,),
        ).fetchone()
        if not slot:
            return False
        if slot["booking_id"] is not None:
            return False
        spot_id = slot["spot_id"]

        # Проверяем пересечение с другими интервалами (включая booked/free)
        conflict = c.execute(
            """SELECT 1 FROM spot_availability
                 WHERE spot_id=? AND id<>?
                   AND NOT (end_time <= ? OR start_time >= ?)
                 LIMIT 1""",
            (
                spot_id,
                slot_id,
                start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        ).fetchone()
        if conflict:
            return False

        c.execute(
            "UPDATE spot_availability SET start_time=?, end_time=? WHERE id=?",
            (
                start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                slot_id,
            ),
        )
        ok = c.rowcount > 0

    # Если слот свободный — попробуем схлопнуть соседние свободные интервалы
    if ok:
        try:
            merge_free_availability(spot_id)
        except Exception:
            pass
    return ok


def admin_delete_availability(slot_id: int) -> bool:
    """Админ удаляет слот availability (только если booking_id IS NULL)."""
    spot_id = None
    with get_connection() as conn:
        c = conn.cursor()
        slot = c.execute(
            "SELECT id, spot_id, booking_id FROM spot_availability WHERE id=?",
            (slot_id,),
        ).fetchone()
        if not slot:
            return False
        if slot["booking_id"] is not None:
            return False
        spot_id = slot["spot_id"]
        c.execute("DELETE FROM spot_availability WHERE id=?", (slot_id,))
        ok = c.rowcount > 0

    if ok and spot_id:
        try:
            merge_free_availability(spot_id)
        except Exception:
            pass
    return ok



def create_booking(customer_id, spot_id, availability_id, start_time, end_time, total_price):
    """Создаёт бронь (pending).

    Важно: в spot_availability храним **реальный** забронированный интервал, а остатки
    (до/после) — отдельными свободными интервалами. Так не возникает перекрытий
    между booked/free, и корректно работает проверка пересечений/склейка.
    """
    with get_connection() as conn:
        c = conn.cursor()
        conn.execute('BEGIN IMMEDIATE')
        start_time = normalize_dt(start_time)
        end_time = normalize_dt(end_time)

        if end_time <= start_time:
            raise ValueError('Invalid interval')
        if start_time < now_local():
            raise ValueError('Start time in past')

        # Цена всегда считается по тарифу из utils.py (не доверяем входному total_price)
        total_price = calculate_price(start_time, end_time)

        # Проверяем что слот свободен (атомарно)
        slot = c.execute('SELECT * FROM spot_availability WHERE id=? AND is_booked=0', (availability_id,)).fetchone()
        if not slot:
            raise ValueError("Slot already booked")

        slot_start = _parse_db_dt(slot['start_time'])
        slot_end = _parse_db_dt(slot['end_time'])

        if start_time < slot_start or end_time > slot_end:
            raise ValueError('Chosen time outside slot')

        # Создаём бронь
        c.execute(
            'INSERT INTO bookings (customer_id,spot_id,availability_id,start_time,end_time,total_price,status) VALUES (?,?,?,?,?,?,?)',
            (
                customer_id, spot_id, availability_id,
                start_time.strftime("%Y-%m-%d %H:%M:%S"),
                end_time.strftime("%Y-%m-%d %H:%M:%S"),
                total_price, 'pending'
            )
        )
        bid = c.lastrowid

        # 1) Текущий availability превращаем в забронированный интервал = времени брони
        c.execute(
            'UPDATE spot_availability SET is_booked=1, booked_by=?, booking_id=?, start_time=?, end_time=? WHERE id=?',
            (
                customer_id, bid,
                start_time.strftime("%Y-%m-%d %H:%M:%S"),
                end_time.strftime("%Y-%m-%d %H:%M:%S"),
                availability_id
            )
        )

        # 2) Остаток ДО
        if start_time > slot_start:
            c.execute(
                'INSERT INTO spot_availability (spot_id,start_time,end_time,is_booked) VALUES (?,?,?,0)',
                (
                    spot_id,
                    slot_start.strftime("%Y-%m-%d %H:%M:%S"),
                    start_time.strftime("%Y-%m-%d %H:%M:%S")
                )
            )

        # 3) Остаток ПОСЛЕ
        if end_time < slot_end:
            c.execute(
                'INSERT INTO spot_availability (spot_id,start_time,end_time,is_booked) VALUES (?,?,?,0)',
                (
                    spot_id,
                    end_time.strftime("%Y-%m-%d %H:%M:%S"),
                    slot_end.strftime("%Y-%m-%d %H:%M:%S")
                )
            )

        _log(c, 'booking_created', booking_id=bid, user_id=customer_id, spot_id=spot_id)
        return bid


def cancel_booking(bid):
    """Отменяет бронь. Освобождает забронированный слот с временем = бронь."""
    spot_id = None
    with get_connection() as conn:
        c = conn.cursor()
        booking = c.execute('SELECT * FROM bookings WHERE id=?',(bid,)).fetchone()
        if not booking:
            return False
        spot_id = booking['spot_id']
        aid = booking['availability_id']
        # Ставим время слота = время брони (не оригинальное!) и освобождаем
        c.execute(
            '''UPDATE spot_availability
               SET is_booked=0, booked_by=NULL, booking_id=NULL, start_time=?, end_time=?
               WHERE id=?''',
            (booking['start_time'], booking['end_time'], aid)
        )
        c.execute("UPDATE bookings SET status='cancelled' WHERE id=?",(bid,))
        _log(c, 'booking_cancelled', booking_id=bid)

    # ВАЖНО: merge открывает новое соединение. Делаем его ПОСЛЕ закрытия транзакции,
    # иначе возможна ошибка SQLite 'database is locked'.
    if spot_id:
        try:
            merge_free_availability(spot_id)
        except Exception:
            pass
    return True

def confirm_booking(bid):
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE bookings SET status='confirmed' WHERE id=? AND status='pending'",(bid,))
        ok = c.rowcount > 0
        if ok: _log(c, 'booking_confirmed', booking_id=bid)
        return ok

def reject_booking(bid):
    return cancel_booking(bid)

def admin_edit_booking_hours(bid, paid_hours):
    """Устанавливает оплаченные часы в брони (с начала).

    Если оплачено меньше полного интервала — остаток превращается в свободный слот.
    Цена пересчитывается по тарифу (utils.calculate_price).
    """
    spot_id = None
    with get_connection() as conn:
        c = conn.cursor()
        booking = c.execute('SELECT * FROM bookings WHERE id=?', (bid,)).fetchone()
        if not booking:
            return False

        spot_id = booking['spot_id']

        book_start = _parse_db_dt(booking['start_time'])
        book_end = _parse_db_dt(booking['end_time'])

        try:
            paid_hours = int(float(paid_hours))
        except Exception:
            return False
        if paid_hours <= 0:
            return False

        full_hours = int(((book_end - book_start).total_seconds() + 3600 - 1) // 3600)
        if paid_hours >= full_hours:
            # оплачен весь интервал — просто пересчитываем цену на всякий случай
            try:
                new_price = calculate_price(book_start, book_end)
            except ValueError:
                return False
            c.execute("UPDATE bookings SET total_price=? WHERE id=?", (new_price, bid))
            try:
                normalize_booking_availability(bid)
            except Exception:
                pass
            _log(c, 'booking_edited', booking_id=bid, details=f"paid_hours={paid_hours} (full)")
            return True

        new_end = book_start + timedelta(hours=paid_hours)

        # 1) В spot_availability освобождаем хвост [new_end, book_end]
        # availability_id должен быть строкой/числом
        aid = booking['availability_id']
        # на всякий случай синхронизируем booked-слот временем брони
        try:
            normalize_booking_availability(bid)
        except Exception:
            pass

        # создаём свободный остаток, если он ещё не существует
        if new_end < book_end:
            c.execute(
                'INSERT INTO spot_availability (spot_id,start_time,end_time,is_booked) VALUES (?,?,?,0)',
                (
                    booking['spot_id'],
                    new_end.strftime("%Y-%m-%d %H:%M:%S"),
                    book_end.strftime("%Y-%m-%d %H:%M:%S")
                )
            )

        # 2) Укорачиваем booked-слот и саму бронь
        c.execute(
            'UPDATE spot_availability SET end_time=? WHERE id=? AND booking_id=?',
            (new_end.strftime("%Y-%m-%d %H:%M:%S"), aid, bid)
        )
        try:
            new_price = calculate_price(book_start, new_end)
        except ValueError:
            return False
        c.execute(
            'UPDATE bookings SET end_time=?, total_price=? WHERE id=?',
            (new_end.strftime("%Y-%m-%d %H:%M:%S"), new_price, bid)
        )

        # Склейку делаем уже после коммита/закрытия соединения (см. ниже)

        _log(c, 'booking_edited', booking_id=bid, details=f"paid_hours={paid_hours} (split)")

    # Склеиваем свободные куски обратно вне транзакции, чтобы не ловить блокировки
    if spot_id:
        try:
            merge_free_availability(spot_id)
        except Exception:
            pass
    return True


def admin_toggle_slot(availability_id):
    with get_connection() as conn:
        c = conn.cursor()
        slot = c.execute('SELECT * FROM spot_availability WHERE id=?',(availability_id,)).fetchone()
        if not slot:
            return None
        # Нельзя переключать слоты, которые привязаны к реальной брони
        if slot['booking_id'] is not None:
            return -1
        new_status = 0 if slot['is_booked'] else 1
        c.execute(
            'UPDATE spot_availability SET is_booked=?, booked_by=NULL, booking_id=NULL WHERE id=?',
            (new_status, availability_id)
        )
        return new_status

def get_booking_by_id(bid):
    with get_connection() as conn:
        r = conn.cursor().execute('''SELECT b.*, ps.spot_number, ps.price_per_hour, ps.supplier_id,
               ps.address,
               u.full_name as customer_name, u.phone as customer_phone, u.username as customer_username,
               u.telegram_id as customer_telegram_id,
               u.license_plate as customer_plate, u.car_brand as customer_car, u.car_color as customer_car_color,
               s.full_name as supplier_name, s.card_number, s.bank, s.phone as supplier_phone,
               s.username as supplier_username, s.telegram_id as supplier_telegram_id
               FROM bookings b JOIN parking_spots ps ON b.spot_id=ps.id
               JOIN users u ON b.customer_id=u.id JOIN users s ON ps.supplier_id=s.id WHERE b.id=?''',(bid,)).fetchone()
        return dict(r) if r else None

def get_user_bookings(uid, status=None):
    with get_connection() as conn:
        q = '''SELECT b.*, ps.spot_number, ps.address, s.full_name as supplier_name, s.card_number, s.bank
               FROM bookings b JOIN parking_spots ps ON b.spot_id=ps.id
               JOIN users s ON ps.supplier_id=s.id WHERE b.customer_id=?'''
        p = [uid]
        if status: q += ' AND b.status=?'; p.append(status)
        q += ' ORDER BY b.created_at DESC'
        return [dict(r) for r in conn.cursor().execute(q, p).fetchall()]

def get_all_bookings(status=None, limit=30):
    with get_connection() as conn:
        q = '''SELECT b.*, ps.spot_number, u.full_name as customer_name, u.phone as customer_phone,
               s.full_name as supplier_name
               FROM bookings b JOIN parking_spots ps ON b.spot_id=ps.id
               JOIN users u ON b.customer_id=u.id JOIN users s ON ps.supplier_id=s.id'''
        p = []
        if status: q += ' WHERE b.status=?'; p.append(status)
        q += ' ORDER BY b.created_at DESC LIMIT ?'; p.append(limit)
        return [dict(r) for r in conn.cursor().execute(q, p).fetchall()]

def get_pending_bookings():
    return get_all_bookings(status='pending')

def get_completed_unreviewed_bookings(uid):
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('''
            SELECT b.*, ps.spot_number FROM bookings b
            JOIN parking_spots ps ON b.spot_id=ps.id
            WHERE b.customer_id=? AND b.status='completed' AND b.reviewed=0
            ORDER BY b.end_time DESC LIMIT 5''',(uid,)).fetchall()]

def get_supplier_bookings(sid):
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('''SELECT b.*, ps.spot_number,
               u.full_name as customer_name, u.phone as customer_phone,
               u.license_plate, u.car_brand, u.car_color
               FROM bookings b JOIN parking_spots ps ON b.spot_id=ps.id
               JOIN users u ON b.customer_id=u.id
               WHERE ps.supplier_id=? AND b.status IN ('pending','confirmed') ORDER BY b.start_time''',(sid,)).fetchall()]

def get_active_bookings_count(uid):
    with get_connection() as conn:
        return conn.cursor().execute("SELECT COUNT(*) FROM bookings WHERE customer_id=? AND status IN ('pending','confirmed')",(uid,)).fetchone()[0]


# ==================== REVIEWS ====================
def create_review(booking_id, reviewer_id, spot_id, supplier_id, rating, comment=''):
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('INSERT INTO reviews (booking_id,reviewer_id,spot_id,supplier_id,rating,comment) VALUES (?,?,?,?,?,?)',
                  (booking_id, reviewer_id, spot_id, supplier_id, rating, comment))
        c.execute('UPDATE bookings SET reviewed=1 WHERE id=?',(booking_id,))
        return c.lastrowid

def get_spot_rating(spot_id):
    with get_connection() as conn:
        r = conn.cursor().execute('SELECT AVG(rating) as avg_r, COUNT(*) as cnt FROM reviews WHERE spot_id=?',(spot_id,)).fetchone()
        return (round(r['avg_r'],1) if r['avg_r'] else 0, r['cnt'])

def get_supplier_rating(supplier_id):
    with get_connection() as conn:
        r = conn.cursor().execute('SELECT AVG(rating) as avg_r, COUNT(*) as cnt FROM reviews WHERE supplier_id=?',(supplier_id,)).fetchone()
        return (round(r['avg_r'],1) if r['avg_r'] else 0, r['cnt'])

def get_spot_reviews(spot_id, limit=10):
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('''
            SELECT r.*, u.full_name as reviewer_name FROM reviews r
            JOIN users u ON r.reviewer_id=u.id WHERE r.spot_id=? ORDER BY r.created_at DESC LIMIT ?''',(spot_id,limit)).fetchall()]

def get_supplier_reviews(supplier_id, limit=10):
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('''
            SELECT r.*, u.full_name as reviewer_name, ps.spot_number FROM reviews r
            JOIN users u ON r.reviewer_id=u.id JOIN parking_spots ps ON r.spot_id=ps.id
            WHERE r.supplier_id=? ORDER BY r.created_at DESC LIMIT ?''',(supplier_id,limit)).fetchall()]


# ==================== BLACKLIST ====================
def add_to_blacklist(user_id, blocked_user_id, reason=''):
    with get_connection() as conn:
        try:
            conn.cursor().execute('INSERT INTO user_blacklist (user_id,blocked_user_id,reason) VALUES (?,?,?)',
                                  (user_id, blocked_user_id, reason))
            return True
        except sqlite3.IntegrityError: return False

def remove_from_blacklist(user_id, blocked_user_id):
    with get_connection() as conn:
        return conn.cursor().execute('DELETE FROM user_blacklist WHERE user_id=? AND blocked_user_id=?',
                                     (user_id, blocked_user_id)).rowcount > 0

def is_blacklisted_either(uid1, uid2):
    with get_connection() as conn:
        return conn.cursor().execute(
            'SELECT 1 FROM user_blacklist WHERE (user_id=? AND blocked_user_id=?) OR (user_id=? AND blocked_user_id=?)',
            (uid1, uid2, uid2, uid1)).fetchone() is not None

def get_user_blacklist(user_id):
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('''
            SELECT bl.*, u.full_name as blocked_name, u.username as blocked_username
            FROM user_blacklist bl JOIN users u ON bl.blocked_user_id=u.id
            WHERE bl.user_id=? ORDER BY bl.created_at DESC''',(user_id,)).fetchall()]


# ==================== NOTIFICATIONS ====================
def create_spot_notification(user_id, desired_date=None, start_time=None, end_time=None, spot_id=None, notify_any=True):
    with get_connection() as conn:
        return conn.cursor().execute('INSERT INTO spot_notifications (user_id,spot_id,desired_date,start_time,end_time,notify_any) VALUES (?,?,?,?,?,?)',
            (user_id, spot_id, desired_date, start_time, end_time, int(notify_any))).lastrowid

def get_matching_notifications(spot_id, start_time, end_time):
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('''SELECT sn.*, u.telegram_id FROM spot_notifications sn JOIN users u ON sn.user_id=u.id
               WHERE sn.is_active=1 AND (sn.notify_any=1 OR sn.spot_id=?)
               AND (sn.desired_date IS NULL OR (sn.desired_date >= ? AND sn.desired_date <= ?))''',
               (spot_id, start_time.strftime("%Y-%m-%d"), end_time.strftime("%Y-%m-%d"))).fetchall()]

def deactivate_notification(nid):
    with get_connection() as conn: return conn.cursor().execute('UPDATE spot_notifications SET is_active=0 WHERE id=?',(nid,)).rowcount > 0
def get_user_notifications(uid):
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('SELECT * FROM spot_notifications WHERE user_id=? AND is_active=1 ORDER BY created_at DESC',(uid,)).fetchall()]


# ==================== ADMIN ====================
def create_admin_session(uid, tid):
    with get_connection() as conn:
        c = conn.cursor(); c.execute('DELETE FROM admin_sessions WHERE telegram_id=?',(tid,))
        c.execute('INSERT INTO admin_sessions (user_id,telegram_id) VALUES (?,?)',(uid,tid))
        return c.lastrowid
def get_admin_session(tid):
    with get_connection() as conn:
        r = conn.cursor().execute('SELECT * FROM admin_sessions WHERE telegram_id=?',(tid,)).fetchone()
        return dict(r) if r else None
def delete_admin_session(tid):
    with get_connection() as conn: return conn.cursor().execute('DELETE FROM admin_sessions WHERE telegram_id=?',(tid,)).rowcount > 0
def log_admin_action(action_type, user_id=None, spot_id=None, booking_id=None, details=None):
    with get_connection() as conn:
        conn.cursor().execute('INSERT INTO admin_logs (action_type,user_id,spot_id,booking_id,details) VALUES (?,?,?,?,?)',
            (action_type, user_id, spot_id, booking_id, details))
def get_admin_logs(limit=100):
    with get_connection() as conn:
        return [dict(r) for r in conn.cursor().execute('SELECT * FROM admin_logs ORDER BY created_at DESC LIMIT ?',(limit,)).fetchall()]

def auto_unban_expired():
    with get_connection() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return conn.cursor().execute(
            "UPDATE users SET is_active=1, banned_until=NULL, ban_reason='' WHERE is_active=0 AND banned_until IS NOT NULL AND banned_until < ?",
            (now,)).rowcount

# ==================== STATS ====================
def get_statistics():
    with get_connection() as conn:
        c = conn.cursor(); s = {}
        s['total_users'] = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        s['active_users'] = c.execute('SELECT COUNT(*) FROM users WHERE is_active=1').fetchone()[0]
        s['total_spots'] = c.execute('SELECT COUNT(*) FROM parking_spots WHERE is_available=1').fetchone()[0]
        s['total_bookings'] = c.execute('SELECT COUNT(*) FROM bookings').fetchone()[0]
        s['pending_bookings'] = c.execute("SELECT COUNT(*) FROM bookings WHERE status='pending'").fetchone()[0]
        s['confirmed_bookings'] = c.execute("SELECT COUNT(*) FROM bookings WHERE status='confirmed'").fetchone()[0]
        s['total_revenue'] = c.execute("SELECT COALESCE(SUM(total_price),0) FROM bookings WHERE status='confirmed'").fetchone()[0]
        return s

def get_user_statistics(uid):
    with get_connection() as conn:
        c = conn.cursor(); s = {}
        s['total_bookings'] = c.execute('SELECT COUNT(*) FROM bookings WHERE customer_id=?',(uid,)).fetchone()[0]
        s['total_spots'] = c.execute('SELECT COUNT(*) FROM parking_spots WHERE supplier_id=? AND is_available=1',(uid,)).fetchone()[0]
        return s


def get_slots_by_owner(owner_id: int):
    """Совместимость со старыми вызовами.

    Возвращает все интервалы availability по всем местам владельца.
    """
    with get_connection() as conn:
        c = conn.cursor()
        return [dict(r) for r in c.execute(
            '''SELECT sa.*, ps.spot_number
               FROM spot_availability sa
               JOIN parking_spots ps ON sa.spot_id=ps.id
               WHERE ps.supplier_id=?
               ORDER BY sa.start_time ASC''',
            (owner_id,)
        ).fetchall()]


def update_booking_time(booking_id: int, start_time: str, end_time: str) -> bool:
    """Совместимость. Обновляет время брони, только если она ещё в pending.

    В этом проекте редактирование времени брони делается через admin_edit_booking_hours,
    поэтому здесь оставляем безопасный вариант.
    """
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE bookings SET start_time=?, end_time=? WHERE id=? AND status='pending'",
            (start_time, end_time, booking_id),
        )
        return c.rowcount > 0


def set_slot_address(slot_id: int, address: str) -> bool:
    """Совместимость. Обновляет адрес места (parking_spots)."""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE parking_spots SET address=? WHERE id=?", (address, slot_id))
        return c.rowcount > 0


def merge_free_availability(spot_id: int) -> int:
    """Схлопывает соседние/перекрывающиеся свободные интервалы availability для одного spot_id.
    Возвращает количество выполненных склеек.

    Склеиваем если интервалы соприкасаются (end == next_start) или перекрываются (end > next_start).
    """
    merges = 0
    with get_connection() as conn:
        c = conn.cursor()
        rows = c.execute(
            """SELECT id, start_time, end_time
                 FROM spot_availability
                 WHERE spot_id=? AND is_booked=0
                 ORDER BY start_time ASC""",
            (spot_id,)
        ).fetchall()

        rows = [dict(r) for r in rows]
        i = 0
        while i < len(rows) - 1:
            cur = rows[i]
            nxt = rows[i + 1]
            try:
                cur_start = _parse_db_dt(cur["start_time"])
                cur_end = _parse_db_dt(cur["end_time"])
                nxt_start = _parse_db_dt(nxt["start_time"])
                nxt_end = _parse_db_dt(nxt["end_time"])
            except Exception:
                i += 1
                continue

            # Если нет разрыва — склеиваем
            if cur_end >= nxt_start:
                new_end = max(cur_end, nxt_end)
                c.execute("UPDATE spot_availability SET start_time=?, end_time=? WHERE id=?",
                          (cur_start.strftime("%Y-%m-%d %H:%M:%S"), new_end.strftime("%Y-%m-%d %H:%M:%S"), cur["id"]))
                c.execute("DELETE FROM spot_availability WHERE id=?", (nxt["id"],))
                cur["end_time"] = new_end.strftime("%Y-%m-%d %H:%M:%S")
                rows.pop(i + 1)
                merges += 1
                continue

            i += 1

    return merges





def normalize_booking_availability(bid: int) -> None:
    """На всякий случай синхронизирует spot_availability с временем брони.

    Нужен для старых данных (когда booked-слот мог хранить исходный большой интервал).
    """
    with get_connection() as conn:
        c = conn.cursor()
        b = c.execute('SELECT id, availability_id, start_time, end_time FROM bookings WHERE id=?', (bid,)).fetchone()
        if not b:
            return
        c.execute(
            '''UPDATE spot_availability
               SET start_time=?, end_time=?
               WHERE id=? AND booking_id=?''',
            (b['start_time'], b['end_time'], b['availability_id'], bid)
        )
def get_booking_status(bid: int):
    with get_connection() as conn:
        r = conn.cursor().execute("SELECT id, status, payment_status FROM bookings WHERE id=?", (bid,)).fetchone()
        return dict(r) if r else None


def mark_booking_paid(bid: int) -> bool:
    """Пользователь отметил оплату. Переводим бронь в paid_wait_admin."""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE bookings SET status='paid_wait_admin', payment_status='paid' "
            "WHERE id=? AND status='pending' AND payment_status='unpaid'",
            (bid,)
        )
        ok = c.rowcount > 0
        if ok:
            try:
                normalize_booking_availability(bid)
            except Exception:
                pass
            _log(c, 'booking_paid_marked', booking_id=bid)
        return ok


def confirm_booking_idempotent(bid: int):
    """Идемпотентное подтверждение. Возвращает (result, status).

    result: 'confirmed' | 'already' | 'invalid' | 'not_paid'
    """
    with get_connection() as conn:
        c = conn.cursor()
        b = c.execute("SELECT id, status FROM bookings WHERE id=?", (bid,)).fetchone()
        if not b:
            return False, 'invalid'
        st = b['status']
        if st == 'confirmed':
            return False, 'already'
        if st in ('cancelled', 'expired', 'completed'):
            return False, 'invalid'
        # если хотим требовать оплату — разрешаем только paid_wait_admin
        if st == 'pending':
            return False, 'not_paid'
        c.execute("UPDATE bookings SET status='confirmed' WHERE id=? AND status='paid_wait_admin'", (bid,))
        if c.rowcount > 0:
            _log(c, 'booking_confirmed', booking_id=bid)
            try:
                normalize_booking_availability(bid)
            except Exception:
                pass
            return True, 'confirmed'
        # если параллельно изменили
        b2 = c.execute("SELECT status FROM bookings WHERE id=?", (bid,)).fetchone()
        if b2 and b2['status'] == 'confirmed':
            return False, 'already'
        return False, 'invalid'


def expire_unpaid_bookings(timeout_minutes: int = 30):
    """Истекает неоплаченные брони pending/unpaid старше timeout_minutes.

    Возвращает список dict: {booking_id, customer_telegram_id}
    """
    expired = []
    # created_at в SQLite задаётся CURRENT_TIMESTAMP (UTC). Поэтому сравниваем в UTC,
    # иначе при локальной TZ (например UTC+3) брони будут "истекать" сразу.
    cutoff = (datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=timeout_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    spot_ids_to_merge = set()
    with get_connection() as conn:
        c = conn.cursor()
        # Блокируем запись, чтобы не было гонок с оплатой/отменой
        conn.execute("BEGIN IMMEDIATE")
        rows = c.execute(
            '''SELECT b.id, b.availability_id, b.spot_id, b.start_time, b.end_time,
                      u.telegram_id as customer_telegram_id
               FROM bookings b
               JOIN users u ON b.customer_id=u.id
               WHERE b.status='pending' AND b.payment_status='unpaid' AND b.created_at <= ?''',
            (cutoff,)
        ).fetchall()

        for r in rows:
            bid = r['id']
            # переводим в expired
            c.execute("UPDATE bookings SET status='expired' WHERE id=? AND status='pending'", (bid,))
            if c.rowcount == 0:
                continue
            # освобождаем availability до времени брони и чистим привязку
            c.execute(
                '''UPDATE spot_availability
                   SET is_booked=0, booked_by=NULL, booking_id=NULL, start_time=?, end_time=?
                   WHERE id=?''',
                (r['start_time'], r['end_time'], r['availability_id'])
            )
            _log(c, 'booking_expired', booking_id=bid, spot_id=r['spot_id'])
            spot_ids_to_merge.add(r['spot_id'])
            expired.append({'booking_id': bid, 'customer_telegram_id': r['customer_telegram_id']})
    # Склеиваем свободные интервалы уже после транзакции
    for sid in spot_ids_to_merge:
        try:
            merge_free_availability(sid)
        except Exception:
            pass
    return expired


def get_nearest_free_slots(limit: int = 10, days: int = 7):
    """Возвращает ближайшие свободные интервалы на ближайшие days дней.
    Адрес возвращаем, но UI может скрыть до подтверждения.
    """
    with get_connection() as conn:
        c = conn.cursor()
        now = now_local().strftime("%Y-%m-%d %H:%M:%S")
        to = (now_local() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = c.execute(
            '''SELECT sa.id as availability_id, sa.spot_id, sa.start_time, sa.end_time,
                      ps.spot_number, ps.price_per_hour, ps.address, ps.supplier_id
               FROM spot_availability sa
               JOIN parking_spots ps ON sa.spot_id = ps.id
               WHERE sa.is_booked=0 AND ps.is_available=1
                 AND sa.start_time >= ? AND sa.start_time <= ?
               ORDER BY sa.start_time ASC
               LIMIT ?''',
            (now, to, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def cleanup_old_bookings(days: int = 30) -> int:
    """Удаляет старые expired/cancelled брони старше days дней. Возвращает кол-во."""
    with get_connection() as conn:
        c = conn.cursor()
        # created_at хранится в UTC (CURRENT_TIMESTAMP)
        cutoff = (datetime.utcnow().replace(second=0, microsecond=0) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            """DELETE FROM bookings
                 WHERE status IN ('expired','cancelled')
                   AND created_at < ?""",
            (cutoff,)
        )
        return c.rowcount


def get_booking_full(bid: int):
    """Бронь с данными места, адреса, клиента и поставщика."""
    with get_connection() as conn:
        c = conn.cursor()
        row = c.execute(
            '''SELECT b.*, ps.spot_number, ps.address, ps.supplier_id,
                      cu.telegram_id as customer_telegram_id, cu.full_name as customer_name, cu.phone as customer_phone, cu.username as customer_username,
                      su.full_name as supplier_name, su.phone as supplier_phone, su.username as supplier_username, su.card_number as supplier_card, su.bank as supplier_bank
               FROM bookings b
               JOIN parking_spots ps ON b.spot_id = ps.id
               JOIN users cu ON b.customer_id = cu.id
               LEFT JOIN users su ON ps.supplier_id = su.id
               WHERE b.id=?''',
            (bid,)
        ).fetchone()
        return dict(row) if row else None


def decline_payment(bid: int, reason: str = "") -> bool:
    """Админ отклонил оплату: возвращаем бронь в pending/unpaid если она в paid_wait_admin."""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE bookings SET status='pending', payment_status='unpaid' WHERE id=? AND status='paid_wait_admin'",
            (bid,)
        )
        ok = c.rowcount > 0
        if ok:
            _log(c, 'payment_declined', booking_id=bid)
        return ok


def init_db():
    """Совместимость со старыми импортами."""
    return init_database()


# ==================== SLOT CONFIRMS (FSM fallback) ====================
import uuid


def create_slot_confirm(user_id: int, spot_number: str, start_time: str, end_time: str, price: float) -> str:
    """Создаёт запись подтверждения слота.
    user_id здесь — telegram_id пользователя (callback.from_user.id).
    """
    cid = str(uuid.uuid4())
    created_at = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO slot_confirms (id, user_id, spot_number, start_time, end_time, price, created_at) VALUES (?,?,?,?,?,?,?)",
            (cid, int(user_id), str(spot_number), str(start_time), str(end_time), float(price), created_at),
        )
    return cid


# Алиас — в некоторых версиях кода использовалось другое имя
def create_spot_confirm(user_id: int, spot_number: str, start_time: str, end_time: str, price: float) -> str:
    return create_slot_confirm(user_id, spot_number, start_time, end_time, price)


def get_slot_confirm(cid: str):
    with get_connection() as conn:
        c = conn.cursor()
        row = c.execute(
            "SELECT id, user_id, spot_number, start_time, end_time, price, created_at FROM slot_confirms WHERE id=?",
            (cid,),
        ).fetchone()
        return dict(row) if row else None


def delete_slot_confirm(cid: str):
    with get_connection() as conn:
        conn.cursor().execute("DELETE FROM slot_confirms WHERE id=?", (cid,))


# ==================== COMPAT HELPERS FOR OLD UI ====================
def create_spot(user_telegram_id: int, spot_number: str, address: str | None = None) -> int:
    """Создаёт/находит место по spot_number для пользователя по telegram_id."""
    u = get_user_by_telegram_id(int(user_telegram_id))
    if not u:
        raise ValueError("User not registered")
    return get_or_create_spot(u["id"], str(spot_number), address=address)


def add_availability(spot_id: int, start_time: str, end_time: str, price: float | None = None) -> int:
    """Добавляет слот доступности; при необходимости обновляет цену места."""
    if price is not None:
        with get_connection() as conn:
            conn.cursor().execute(
                "UPDATE parking_spots SET price_per_hour=? WHERE id=?",
                (float(price), int(spot_id)),
            )
    return create_spot_availability(int(spot_id), start_time, end_time)
