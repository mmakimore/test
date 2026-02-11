"""
Клавиатуры ParkingBot
"""
from datetime import datetime, timedelta
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton,
                           ReplyKeyboardRemove)
from utils import get_next_days, now_local
from config import FIXED_ADDRESS

# ==================== MAIN MENU ====================
def get_main_menu_keyboard(is_admin=False):
    buttons = [
        [KeyboardButton(text="📅 Найти место"), KeyboardButton(text="⏱ Ближайшие слоты")],
        [KeyboardButton(text="➕ Добавить место")],
        [KeyboardButton(text="📋 Мои бронирования"), KeyboardButton(text="🏠 Мои слоты")],
        [KeyboardButton(text="📊 Тарифы")],
        [KeyboardButton(text="ℹ️ О сервисе"), KeyboardButton(text="📜 Правила")],
        [KeyboardButton(text="👤 Профиль")],
    ]
    if is_admin:
        buttons.append([KeyboardButton(text="🔑 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)

def get_cancel_menu_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Главное меню")]], resize_keyboard=True)

def get_phone_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📱 Отправить контакт", request_contact=True)],
        [KeyboardButton(text="❌ Отмена")]
    ], resize_keyboard=True)

# ==================== DATES / TIMES ====================
def get_dates_keyboard(prefix):
    buttons = []
    days = get_next_days(7)
    for i in range(0, len(days), 3):
        buttons.append([InlineKeyboardButton(text=days[j][:5], callback_data=f"{prefix}_{days[j]}")
                       for j in range(i, min(i+3, len(days)))])
    buttons.append([InlineKeyboardButton(text="📅 Все доступные", callback_data=f"{prefix}_all")])
    buttons.append([InlineKeyboardButton(text="✍️ Ввести дату", callback_data=f"{prefix}_manual")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_time_slots_keyboard(prefix, min_dt=None):
    """Клавиатура времени.
    Если min_dt задан и это сегодня — убираем варианты раньше текущего времени.
    """
    # Раньше бот ограничивал время 06:00–23:00.
    # Сейчас по умолчанию доступно круглосуточно (00:00–23:00).
    times = [f"{h:02d}:00" for h in range(0, 24)]

    try:
        if min_dt:
            if isinstance(min_dt, str):
                min_dt = datetime.fromisoformat(min_dt)
            # сравниваем по той же дате
            base_date = min_dt.date()
            filtered = []
            for t in times:
                hh, mm = map(int, t.split(":"))
                dt_val = datetime(base_date.year, base_date.month, base_date.day, hh, mm)
                if dt_val >= min_dt.replace(second=0, microsecond=0):
                    filtered.append(t)
            times = filtered
    except Exception:
        pass

    buttons = []
    for i in range(0, len(times), 4):
        buttons.append([InlineKeyboardButton(text=times[j], callback_data=f"{prefix}_{times[j]}")
                       for j in range(i, min(i+4, len(times)))])
    buttons.append([InlineKeyboardButton(text="✍️ Ввести", callback_data=f"{prefix}_manual")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==================== SLOTS ====================
def get_available_slots_keyboard(slots):
    buttons = []
    for slot in slots[:20]:
        start = datetime.fromisoformat(slot['start_time'])
        end = datetime.fromisoformat(slot['end_time'])
        sd = start.strftime('%d.%m')
        ed = end.strftime('%d.%m')
        if sd == ed:
            date_text = f"{sd} {start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
        else:
            date_text = f"{sd}-{ed} {start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
        # Номер места скрываем до подтверждения оплаты — показываем адрес и время.
        addr = FIXED_ADDRESS
        if len(addr) > 26:
            addr = addr[:25] + "…"
        text = f"📍 {addr} | {date_text}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"slot_{slot['id']}")])
    buttons.append([InlineKeyboardButton(text="📅 Фильтр по дате", callback_data="search_filter")])
    buttons.append([InlineKeyboardButton(text="🔔 Уведомить", callback_data="notify_available")])
    buttons.append([InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_no_slots_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Уведомить о появлении", callback_data="notify_available")],
        [InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")]
    ])

# ==================== MY SPOTS ====================
def get_my_spots_keyboard(spots):
    buttons = []
    for spot in spots:
        buttons.append([InlineKeyboardButton(text=f"🏠 {spot['spot_number']}", callback_data=f"myspot_{spot['id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_spot_detail_keyboard(spot_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Добавить слот", callback_data=f"addslot_{spot_id}")],
        [InlineKeyboardButton(text="❌ Удалить место", callback_data=f"delspot_{spot_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_spots")]
    ])

def get_slot_actions_keyboard(slot_id, is_booked):
    buttons = []
    if not is_booked:
        buttons.append([InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"editslot_{slot_id}")])
        buttons.append([InlineKeyboardButton(text="🗑 Удалить слот", callback_data=f"delslot_{slot_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_spot_detail")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== BOOKINGS ====================
def get_booking_detail_keyboard(booking, user_id):
    buttons = []
    if booking['status'] in ('pending','confirmed'):
        buttons.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_booking_{booking['id']}")])
    if booking['status'] == 'completed' and not booking.get('reviewed'):
        buttons.append([InlineKeyboardButton(text="⭐ Отзыв", callback_data=f"review_start_{booking['id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_bookings")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== BANKS ====================
def get_bank_keyboard():
    from config import BANKS
    buttons = []
    for i in range(0, len(BANKS), 2):
        row = [InlineKeyboardButton(text=BANKS[j], callback_data=f"bank_{BANKS[j]}")
               for j in range(i, min(i+2, len(BANKS)))]
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== CONFIRM ====================
def get_confirm_keyboard(prefix):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"{prefix}_yes"),
         InlineKeyboardButton(text="❌ Нет", callback_data=f"{prefix}_no")]
    ])

# ==================== NOTIFY ====================
def get_notify_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 На дату", callback_data="notify_date")],
        [InlineKeyboardButton(text="🔔 На любое", callback_data="notify_any")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

# ==================== REVIEWS ====================
def get_rating_keyboard(booking_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{'⭐'*i}", callback_data=f"rate_{booking_id}_{i}") for i in range(1,6)]
    ])

def get_review_skip_comment_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏩ Пропустить", callback_data="review_nocomment")]
    ])

# ==================== PROFILE ====================
def get_profile_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Имя", callback_data="edit_name"),
         InlineKeyboardButton(text="📞 Телефон", callback_data="edit_phone")],
        [InlineKeyboardButton(text="🚗 Авто", callback_data="edit_car")],
        [InlineKeyboardButton(text="💳 Карта", callback_data="edit_card")],
        [InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")]
    ])

# ==================== ADMIN ====================
def get_admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Заявки на бронь", callback_data="admin_pending")],
        [InlineKeyboardButton(text="📊 Все бронирования", callback_data="admin_all_bookings")],
        [InlineKeyboardButton(text="🏠 Управление слотами", callback_data="admin_slots")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="💾 Выгрузить базу", callback_data="admin_export_db")],
        [InlineKeyboardButton(text="📊 Выгрузить Excel", callback_data="admin_export_excel")],
        [InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")]
    ])

def get_admin_booking_keyboard(bid, status):
    buttons = []
    if status == 'pending':
        buttons.append([
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"adm_confirm_{bid}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_reject_{bid}")
        ])
    if status in ('pending','confirmed'):
        buttons.append([InlineKeyboardButton(text="✏️ Редактировать часы", callback_data=f"adm_edit_{bid}")])
        buttons.append([InlineKeyboardButton(text="🚫 Отменить бронь", callback_data=f"adm_cancel_{bid}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_pending")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_slot_actions_keyboard(slot_id: int, spot_id: int, is_booked: bool, can_edit: bool):
    """Клавиатура управления одним availability-слотом в админке.

    can_edit=True означает, что слот не привязан к брони (booking_id IS NULL),
    и его можно редактировать/удалять.
    """
    buttons = []
    if can_edit:
        buttons.append([
            InlineKeyboardButton(text="⏱ Начало", callback_data=f"adm_editstart_{slot_id}"),
            InlineKeyboardButton(text="⏱ Конец", callback_data=f"adm_editend_{slot_id}"),
        ])
        buttons.append([
            InlineKeyboardButton(text="🗑 Удалить слот", callback_data=f"adm_delslot_{slot_id}")
        ])

    toggle_text = "🔓 Сделать свободным" if is_booked else "🔒 Сделать забронированным"
    buttons.append([InlineKeyboardButton(text=toggle_text, callback_data=f"adm_toggle_{slot_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"adm_spot_{spot_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_user_admin_actions_keyboard(uid, user):
    buttons = []
    if user['role'] != 'admin':
        buttons.append([InlineKeyboardButton(text="👑 Сделать админом", callback_data=f"set_admin_{uid}")])
    else:
        buttons.append([InlineKeyboardButton(text="👤 Убрать админа", callback_data=f"set_user_{uid}")])
    if not user.get('is_active'):
        buttons.append([InlineKeyboardButton(text="🔓 Разбанить", callback_data=f"unban_{uid}")])
    else:
        buttons.append([InlineKeyboardButton(text="🚫 Бан", callback_data=f"ban_menu_{uid}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_ban_duration_keyboard(uid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1ч", callback_data=f"ban_{uid}_1"),
         InlineKeyboardButton(text="24ч", callback_data=f"ban_{uid}_24")],
        [InlineKeyboardButton(text="7д", callback_data=f"ban_{uid}_168"),
         InlineKeyboardButton(text="30д", callback_data=f"ban_{uid}_720")],
        [InlineKeyboardButton(text="♾ Навсегда", callback_data=f"ban_{uid}_0")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_users")]
    ])

def get_broadcast_target_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Все пользователи", callback_data="broadcast_all")],
        [InlineKeyboardButton(text="✅ Только активные", callback_data="broadcast_active")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])


def address_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="", callback_data="noop")],
        [InlineKeyboardButton(text="", callback_data="noop")],
        [InlineKeyboardButton(text="", callback_data="noop")]
    ])


def booking_payment_keyboard(booking_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"booking_paid_{booking_id}")],
        [InlineKeyboardButton(text="❌ Отменить бронь", callback_data=f"booking_cancel_{booking_id}")]
    ])


def admin_payment_review_keyboard(booking_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"adm_pay_confirm_{booking_id}")],
        [InlineKeyboardButton(text="❌ Отклонить оплату", callback_data=f"adm_pay_decline_{booking_id}")]
    ])
