"""
Утилиты и валидация ParkingBot
"""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PHONE_REGEX = r'^(\+7|7|8)?[\s\-]?\(?[489][0-9]{2}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}$'

def validate_name(name: str):
    # Требуем "Имя Фамилия" (минимум 2 слова)
    name = re.sub(r"\s+", " ", (name or "").strip())
    if len(name) < 3:
        return False, "❌ Введите имя и фамилию (пример: Иван Иванов)"
    if len(name) > 60:
        return False, "❌ Слишком длинно (макс. 60 символов)"
    parts = name.split(" ")
    if len(parts) < 2:
        return False, "❌ Нужно имя и фамилия (пример: Иван Иванов)"
    for p in parts:
        if not re.match(r"^[A-Za-zА-Яа-яЁё\-]+$", p):
            return False, "❌ Используйте только буквы и дефис (пример: Иван Иванов)"
    return True, name

def validate_phone(phone):
    cleaned = re.sub(r'[^\d+]', '', phone)
    if not re.match(PHONE_REGEX, phone):
        return False, "❌ Неверный формат. +7XXXXXXXXXX или 8XXXXXXXXXX"
    if cleaned.startswith('+7'): cleaned = '8' + cleaned[2:]
    elif cleaned.startswith('7') and len(cleaned) == 11: cleaned = '8' + cleaned[1:]
    if len(cleaned) != 11: return False, "❌ Номер должен содержать 11 цифр"
    return True, cleaned

def luhn_check(card):
    digits = [int(d) for d in card]
    odd = digits[-1::-2]; even = digits[-2::-2]
    total = sum(odd) + sum(d*2-9 if d*2>9 else d*2 for d in even)
    return total % 10 == 0

def validate_card(card):
    cleaned = re.sub(r"\D", "", card or "")
    if len(cleaned) != 16:
        return False, "❌ Номер карты: 16 цифр"
    from config import STRICT_CARD_VALIDATION, MIR_ONLY, ALLOWED_TEST_CARDS
    if STRICT_CARD_VALIDATION and not luhn_check(cleaned):
        return False, "❌ Неверный номер карты"
    if MIR_ONLY:
        prefix = int(cleaned[:4])
        is_mir = 2200 <= prefix <= 2204
        if (not is_mir) and (cleaned not in ALLOWED_TEST_CARDS):
            return False, "❌ Только карты МИР (начинается на 2200–2204)"
    return True, cleaned

def validate_date(date_str):
    if not re.match(r'^(0[1-9]|[12]\d|3[01])\.(0[1-9]|1[0-2])\.\d{4}$', date_str):
        return False, None
    try:
        parsed = datetime.strptime(date_str, "%d.%m.%Y")
        if parsed.date() < datetime.now().date(): return False, None
        return True, parsed
    except ValueError: return False, None

def validate_time(time_str):
    """Валидация времени.

    На текущем проекте работаем только с почасовыми слотами, поэтому минуты должны быть 00.
    """
    m = re.match(r'^([01]\d|2[0-3]):([0-5]\d)$', (time_str or '').strip())
    if not m:
        return False, None
    hh = int(m.group(1)); mm = int(m.group(2))
    if mm != 0:
        return False, None
    return True, f"{hh:02d}:00"


def validate_spot_number(s):
    s = s.strip()
    if len(s) < 1: return False, "❌ Номер не может быть пустым"
    if len(s) > 10: return False, "❌ Максимум 10 символов"
    return True, s

def validate_license_plate(p):
    """Принимаем любой госномер.

    Ранее была строгая валидация РФ-формата (А123ВС77), из-за чего пользователи
    не могли вводить номера в другом формате. Теперь допускаем любой ввод,
    минимально очищая пробелы/дефисы и ограничивая длину.
    """
    p = (p or "").strip().upper()
    # Убираем пробелы и дефисы — так удобнее читать и хранить
    p = re.sub(r"[\s\-]", "", p)
    if len(p) < 3:
        return False, "❌ Номер слишком короткий"
    if len(p) > 20:
        return False, "❌ Номер слишком длинный"
    # Разрешаем буквы/цифры (латиница/кириллица)
    if not re.fullmatch(r"[0-9A-ZА-ЯЁ]+", p):
        return False, "❌ Используйте только буквы и цифры"
    return True, p
def validate_car_brand(b):
    b = b.strip()
    if len(b) < 2: return False, "❌ Слишком короткое"
    if len(b) > 50: return False, "❌ Слишком длинное"
    return True, b

def validate_car_color(c):
    c = c.strip()
    if len(c) < 2: return False, "❌ Слишком короткий"
    if len(c) > 30: return False, "❌ Слишком длинный"
    return True, c

def format_datetime(dt):
    if isinstance(dt, str): dt = datetime.fromisoformat(dt)
    return dt.strftime("%d.%m.%Y %H:%M")

def format_date(dt):
    if isinstance(dt, str): dt = datetime.fromisoformat(dt)
    return dt.strftime("%d.%m.%Y")

def parse_datetime(date_str, time_str):
    try: return datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
    except ValueError: return None

def get_next_days(count=7):
    # Даты показываем в локальной TZ (см. now_local), чтобы не было сдвигов на хостинге (UTC vs локальное).
    today = now_local()
    return [(today + timedelta(days=i)).strftime("%d.%m.%Y") for i in range(count)]

def get_price_per_hour(hours):
    """Возвращает усреднённую цену за час по тарифной сетке (для справки)."""
    from config import PRICE_TOTAL_BY_HOURS, EXTRA_HOUR_PRICE_AFTER_24
    h = int(max(1, hours))
    if h in PRICE_TOTAL_BY_HOURS:
        return int(round(PRICE_TOTAL_BY_HOURS[h] / h))
    # >24ч: считаем как сутки + доп. часы
    max_h = 24
    base = int(PRICE_TOTAL_BY_HOURS[max_h])
    total = base + (h - max_h) * int(EXTRA_HOUR_PRICE_AFTER_24)
    return int(round(total / h))

def _hours_ceil(start: datetime, end: datetime) -> int:
    seconds = (end - start).total_seconds()
    if seconds <= 0:
        return 0
    return int((seconds + 3600 - 1) // 3600)

def calculate_price(start, end):
    """Считает итоговую цену.

    Правила:
    - Днём действует таблица PRICE_TOTAL_BY_HOURS (итог за N часов).
    - Ночью (20:00–08:00) действует отдельный тариф:
        • 10ч → 600₽, 11ч → 650₽, 12ч → 700₽
        • минимальная стоимость ночного тарифа — 600₽
        • ночной тариф можно брать от 1 часа, но ночная часть всегда минимум 600₽
    - Если бронь затрагивает и день, и ночь — стоимость = (день) + (ночь).

    Минуты округляются вверх до часа (как и раньше).
    """
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
    if isinstance(end, str):
        end = datetime.fromisoformat(end)

    if end <= start:
        return 0

    from config import (
        PRICE_TOTAL_BY_HOURS, EXTRA_HOUR_PRICE_AFTER_24,
        NIGHT_START, NIGHT_END, NIGHT_MIN_PRICE, NIGHT_TOTAL_BY_HOURS,
    )

    def _day_price(h: int) -> int:
        h = int(max(0, h))
        if h <= 0:
            return 0
        if h in PRICE_TOTAL_BY_HOURS:
            return int(PRICE_TOTAL_BY_HOURS[h])
        days = h // 24
        rem = h % 24
        total = days * int(PRICE_TOTAL_BY_HOURS[24])
        if rem:
            if rem in PRICE_TOTAL_BY_HOURS:
                total += int(PRICE_TOTAL_BY_HOURS[rem])
            else:
                total += rem * int(EXTRA_HOUR_PRICE_AFTER_24)
        return int(total)

    def _night_price(h: int) -> int:
        """Цена за h ночных часов (минимум NIGHT_MIN_PRICE)."""
        h = int(max(0, h))
        if h <= 0:
            return 0
        # В ТЗ явно указаны 10/11/12, ниже 10 — минималка.
        if h <= 10:
            return int(NIGHT_MIN_PRICE)
        if h in NIGHT_TOTAL_BY_HOURS:
            return int(NIGHT_TOTAL_BY_HOURS[h])
        # 12+ считаем как максимум ночи
        return int(NIGHT_TOTAL_BY_HOURS.get(12, NIGHT_MIN_PRICE))

    # --- Разбиваем интервал по границам 08:00/20:00, чтобы корректно отделить день/ночь.
    def _parse_hm(s: str):
        hh, mm = map(int, s.split(":"))
        return hh, mm

    ns_h, ns_m = _parse_hm(NIGHT_START)
    ne_h, ne_m = _parse_hm(NIGHT_END)

    # Границы тарифов (день = [NIGHT_END, NIGHT_START), ночь = остальное)
    # Для аккуратного разбиения собираем все точки переключения в (start, end)
    boundaries = []
    start_date = start.date()
    end_date = end.date()
    d = start_date
    while d <= end_date:
        b1 = datetime(d.year, d.month, d.day, ne_h, ne_m)  # 08:00
        b2 = datetime(d.year, d.month, d.day, ns_h, ns_m)  # 20:00
        if start < b1 < end:
            boundaries.append(b1)
        if start < b2 < end:
            boundaries.append(b2)
        d += timedelta(days=1)

    boundaries.sort()
    points = [start] + boundaries + [end]

    day_hours = 0
    night_segments_hours: list[int] = []

    def _is_night(dt: datetime) -> bool:
        t = dt.time()
        # ночь: [20:00..24:00) или [00:00..08:00)
        return (t.hour, t.minute) >= (ns_h, ns_m) or (t.hour, t.minute) < (ne_h, ne_m)

    for a, b in zip(points, points[1:]):
        if b <= a:
            continue
        h = _hours_ceil(a, b)
        if h <= 0:
            continue
        if _is_night(a):
            night_segments_hours.append(h)
        else:
            day_hours += h

    total_night_hours = sum(night_segments_hours)
    mixed = day_hours > 0 and total_night_hours > 0

    total = _day_price(day_hours)
    # Ночь считаем по каждому сегменту (если бронь затрагивает два разных ночных окна в длинных бронях).
    for h in night_segments_hours:
        p = _night_price(h)
        # В смешанной брони — минималка ночи действует всегда (даже если h=1)
        if mixed:
            p = max(int(NIGHT_MIN_PRICE), p)
        total += p

    return int(total)

def format_price_info():
    """Строка с тарифами для показа пользователю"""
    return (
        "💰 <b>Тарифы:</b>\n"
        "• 1-3ч → 150₽/ч\n"
        "• 4-6ч → 120₽/ч\n"
        "• 7-10ч → 90₽/ч\n"
        "• 11-24ч → 60₽/ч\n"
        "• 24ч+ → 60₽/ч"
    )

def mask_card(card):
    if card and len(card) >= 4: return f"****{card[-4:]}"
    return "—"

def now_local():
    """Текущее локальное время в TZ из config.TIMEZONE (naive datetime)."""
    from config import TIMEZONE
    tz = ZoneInfo(TIMEZONE)
    return datetime.now(tz).replace(tzinfo=None, second=0, microsecond=0)

def normalize_dt(dt: datetime) -> datetime:
    """Нормализует datetime: обнуляет секунды/микросекунды."""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.replace(second=0, microsecond=0)



def now_tz(tz_name: str):
    return datetime.now(ZoneInfo(tz_name))

def round_to_step(dt: datetime, step_minutes: int):
    """Округляет вниз к шагу step_minutes."""
    dt = dt.replace(second=0, microsecond=0)
    minutes = (dt.minute // step_minutes) * step_minutes
    return dt.replace(minute=minutes)

def parse_hhmm(s: str):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s.strip())
    if not m:
        raise ValueError("Invalid HH:MM")
    h = int(m.group(1)); mi = int(m.group(2))
    if h<0 or h>23 or mi<0 or mi>59:
        raise ValueError("Invalid HH:MM")
    return h, mi

def is_within_working_hours(start_dt: datetime, end_dt: datetime, start_hhmm: str, end_hhmm: str):
    sh, sm = parse_hhmm(start_hhmm)
    eh, em = parse_hhmm(end_hhmm)

    # Если окно покрывает весь день (00:00–23:59), ограничения по часам фактически нет.
    if sh == 0 and sm == 0 and (eh * 60 + em) >= (23 * 60 + 59):
        return True
    day_start = start_dt.replace(hour=sh, minute=sm, second=0, microsecond=0)
    day_end = start_dt.replace(hour=eh, minute=em, second=0, microsecond=0)
    # если end меньше start (ночной режим) — не поддерживаем
    if day_end <= day_start:
        return False
    return start_dt >= day_start and end_dt <= day_end

def validate_interval(start_dt: datetime, end_dt: datetime, now_dt: datetime, min_minutes: int,
                      working_start: str, working_end: str):
    # На всякий случай приводим всё к naive datetime.
    # Это защищает от ошибки "can't compare offset-naive and offset-aware datetimes"
    # если где-то передали aware.
    if getattr(start_dt, "tzinfo", None):
        start_dt = start_dt.replace(tzinfo=None)
    if getattr(end_dt, "tzinfo", None):
        end_dt = end_dt.replace(tzinfo=None)
    if getattr(now_dt, "tzinfo", None):
        now_dt = now_dt.replace(tzinfo=None)

    if end_dt <= start_dt:
        return False, "❌ Время окончания должно быть позже начала"
    if start_dt < now_dt:
        return False, "❌ Нельзя выбрать время в прошлом"
    dur_min = int((end_dt - start_dt).total_seconds() // 60)
    if dur_min < min_minutes:
        return False, f"❌ Минимальная длительность {min_minutes} минут"
    if not is_within_working_hours(start_dt, end_dt, working_start, working_end):
        return False, f"❌ Доступно только в часы {working_start}–{working_end}"
    return True, ""
