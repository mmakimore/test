"""
Обработчики пользователя ParkingBot
"""
import logging
import re
from datetime import datetime, timedelta

from datetime import datetime

def _to_naive_local(dt: datetime) -> datetime:
    # If dt has tzinfo, drop it to avoid naive/aware compare issues on hosting.
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import database as db
from config import BANKS, MAX_ACTIVE_BOOKINGS, MAX_SPOTS_PER_USER, ABOUT_TEXT, RULES_TEXT, TIME_STEP_MINUTES, WORKING_HOURS_START, WORKING_HOURS_END, MIN_BOOKING_MINUTES, AVAILABILITY_LOOKAHEAD_DAYS, ADMIN_CHECK_USERNAME, CARD_NUMBER, TIMEZONE, FIXED_ADDRESS, PRICE_TOTAL_BY_HOURS, WELCOME_TEXT
from keyboards import *
from utils import *

logger = logging.getLogger(__name__)
router = Router()
def _min_dt_for_date(date_str: str):
    """Если дата = сегодня, возвращает now_local(), иначе None."""
    try:
        d = datetime.strptime(date_str, "%d.%m.%Y").date()
        n = now_local()
        return n if d == n.date() else None
    except Exception:
        return None



# ==================== STATES ====================
class RegistrationStates(StatesGroup):
    waiting_name = State()
    waiting_phone = State()

class CarInfoStates(StatesGroup):
    waiting_license_plate = State()
    waiting_car_brand = State()
    waiting_car_color = State()

class CardInfoStates(StatesGroup):
    waiting_card = State()
    waiting_bank = State()
    waiting_bank_name = State()

class AddSpotStates(StatesGroup):
    waiting_spot_number = State()
    waiting_start_date = State()
    waiting_start_date_manual = State()
    waiting_start_time = State()
    waiting_start_time_manual = State()
    waiting_end_date = State()
    waiting_end_date_manual = State()
    waiting_end_time = State()
    waiting_end_time_manual = State()
    confirming = State()

class SearchStates(StatesGroup):
    waiting_date = State()
    waiting_date_manual = State()
    selecting_slot = State()
    selecting_start_date = State()
    selecting_start_time = State()
    selecting_end_date = State()
    selecting_end_time = State()
    confirming_booking = State()

class NotifyStates(StatesGroup):
    selecting_option = State()
    waiting_date = State()
    waiting_date_manual = State()

class EditProfileStates(StatesGroup):
    waiting_name = State()
    waiting_phone = State()
    waiting_card = State()
    waiting_bank = State()
    waiting_bank_name = State()

class ReviewStates(StatesGroup):
    waiting_rating = State()
    waiting_comment = State()

class AddSlotStates(StatesGroup):
    waiting_start_date = State()
    waiting_start_date_manual = State()
    waiting_start_time = State()
    waiting_start_time_manual = State()
    waiting_end_date = State()
    waiting_end_date_manual = State()
    waiting_end_time = State()
    waiting_end_time_manual = State()

class EditSlotStates(StatesGroup):
    choosing_field = State()
    waiting_start_date = State()
    waiting_start_time = State()
    waiting_end_date = State()
    waiting_end_time = State()

# ==================== HELPERS ====================
def _adm(tid):
    u = db.get_user_by_telegram_id(tid)
    return u and u['role'] == 'admin'

def _cancel_check(text):
    return text and text in ["❌ Отмена", "🔙 Главное меню"]

async def _check_ban(msg_or_cb):
    tid = msg_or_cb.from_user.id
    user = db.get_user_by_telegram_id(tid)
    if not user: return False
    banned, reason, until = db.is_user_banned(user)
    if banned:
        t = "🚫 Вы заблокированы"
        if until: t += f" до {format_datetime(datetime.fromisoformat(until))}"
        else: t += " навсегда"
        if reason: t += f"\n📝 Причина: {reason}"
        if isinstance(msg_or_cb, Message): await msg_or_cb.answer(t, parse_mode="HTML")
        else: await msg_or_cb.answer(t, show_alert=True)
        return True
    return False


# ==================== REGISTRATION ====================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    # Синхронизируем username из Telegram всегда, чтобы он "притягивался" независимо от телефона.
    tg_username = message.from_user.username or ""
    user = db.get_user_by_telegram_id(message.from_user.id)
    if user:
        # Если пользователь поменял username или он был пустым — обновляем.
        try:
            if tg_username and tg_username != (user.get('username') or ""):
                db.update_user(user['id'], username=tg_username)
                user['username'] = tg_username
        except Exception:
            pass
        banned, reason, until = db.is_user_banned(user)
        if banned:
            t = "🚫 Вы заблокированы"
            if until: t += f" до {format_datetime(datetime.fromisoformat(until))}"
            if reason: t += f"\n📝 {reason}"
            await message.answer(t, parse_mode="HTML"); return
        # Приветствие
        try:
            await message.answer(WELCOME_TEXT)
        except Exception:
            pass

        await message.answer(f"👋 <b>{user['full_name']}</b>, выберите действие:",
            reply_markup=get_main_menu_keyboard(user['role']=='admin'), parse_mode="HTML")
        unreviewed = db.get_completed_unreviewed_bookings(user['id'])
        if unreviewed:
            b = unreviewed[0]
            await message.answer(
                f"⭐ Незавершённый отзыв!\n🏠 {b['spot_number']}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ Оставить отзыв", callback_data=f"review_start_{b['id']}")]
                ]))
    else:
        # Запомним username сразу (на случай, если далее будут сообщения без from_user.username)
        await state.update_data(tg_username=tg_username)
        await message.answer(WELCOME_TEXT)
        await message.answer(
            "📝 Введите <b>имя и фамилию</b>:",
            reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        await state.set_state(RegistrationStates.waiting_name)

class PayReceiptStates(StatesGroup):
    waiting_receipt = State()


@router.message(RegistrationStates.waiting_name)
async def reg_name(message: Message, state: FSMContext):
    if _cancel_check(message.text): await state.clear(); await message.answer("Отменено."); return
    ok, r = validate_name(message.text)
    if not ok: await message.answer(r); return
    await state.update_data(full_name=r)
    await message.answer("📞 <b>Номер телефона</b>\n\nНажмите кнопку или введите вручную:",
        reply_markup=get_phone_keyboard(), parse_mode="HTML")
    await state.set_state(RegistrationStates.waiting_phone)

@router.message(RegistrationStates.waiting_phone)
async def reg_phone(message: Message, state: FSMContext):
    if message.text and _cancel_check(message.text):
        await state.clear(); await message.answer("Отменено."); return
    if message.contact:
        phone = message.contact.phone_number
        if phone.startswith('+'): phone = phone[1:]
        if phone.startswith('7') and len(phone) == 11: phone = '8' + phone[1:]
        r = phone
    else:
        if not message.text: return
        ok, r = validate_phone(message.text)
        if not ok: await message.answer(r); return
    data = await state.get_data()
    tg_username = message.from_user.username or data.get('tg_username', "") or ""
    db.create_user(
        telegram_id=message.from_user.id,
        username=tg_username,
        full_name=data['full_name'],
        phone=r
    )
    await state.clear()
    await message.answer(f"✅ <b>Готово!</b>\n\n👤 {data['full_name']}\n📞 {r}",
        reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    # Уведомления о новых пользователях отключены (по ТЗ).


# ==================== NAV ====================
@router.message(F.text == "🔙 Главное меню")
async def go_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🏠", reply_markup=get_main_menu_keyboard(_adm(message.from_user.id)))

@router.message(F.text == "❌ Отмена")
async def cancel_msg(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=get_main_menu_keyboard(_adm(message.from_user.id)))

@router.callback_query(F.data == "cancel")
async def cancel_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await state.clear()
    try: await callback.message.edit_text("❌ Отменено.")
    except: pass
    await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))

@router.callback_query(F.data == "main_menu")
async def menu_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await state.clear()
    try: await callback.message.edit_text("🏠")
    except: pass
    await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))


# ==================== О СЕРВИСЕ / ПРАВИЛА ====================
@router.message(F.text == "📊 Тарифы")
async def show_tariffs(message: Message):
    # Таблица дневных тарифов (из config.PRICE_TOTAL_BY_HOURS)
    from config import PRICE_TOTAL_BY_HOURS, NIGHT_TOTAL_BY_HOURS, NIGHT_START, NIGHT_END, NIGHT_MIN_HOURS, NIGHT_MIN_PRICE

    rows = []
    for h in sorted(PRICE_TOTAL_BY_HOURS.keys()):
        total = int(PRICE_TOTAL_BY_HOURS[h])
        per_h = int(round(total / h))
        rows.append((h, total, per_h))
    lines = []
    lines.append("📊 <b>Тарифы (день)</b>")
    lines.append(f"📍 {FIXED_ADDRESS}")
    lines.append("")
    lines.append("<pre>")
    lines.append("| Часы | Итоговая цена | Цена за час |")
    lines.append("| ---- | ------------- | ----------- |")
    for h,total,per_h in rows:
        lines.append(f"| {str(h).ljust(4)} | {str(total).ljust(13)} | {str(per_h).ljust(11)} |")
    lines.append("</pre>")

    # Ночные тарифы
    lines.append("")
    lines.append(f"🌙 <b>Ночь</b>: {NIGHT_START}–{NIGHT_END}")
    lines.append(f"Минимум ночью: <b>{NIGHT_MIN_HOURS} часов</b> (если бронь только ночью)")
    lines.append(f"Если бронь заходит в ночь из дня — ночная часть считается отдельно, минимум <b>{NIGHT_MIN_PRICE}₽</b> даже за 1 час.")
    lines.append("<pre>")
    lines.append("| Часы | Итоговая цена |")
    lines.append("| ---- | ------------- |")
    for h in sorted(NIGHT_TOTAL_BY_HOURS.keys()):
        lines.append(f"| {str(h).ljust(4)} | {str(int(NIGHT_TOTAL_BY_HOURS[h])).ljust(13)} |")
    lines.append("</pre>")

    lines.append("По всем вопросам пишите @timofey_zhuravel")
    await message.answer("\n".join(lines), parse_mode="HTML")

@router.message(F.text == "ℹ️ О сервисе")
async def about_service(message: Message):
    await message.answer(ABOUT_TEXT, parse_mode="HTML")

@router.message(F.text == "📜 Правила")
async def rules(message: Message):
    await message.answer(RULES_TEXT, parse_mode="HTML")


# ==================== SEARCH ====================
@router.message(F.text == "📅 Найти место")
async def search_start(message: Message, state: FSMContext):
    if await _check_ban(message): return
    user = db.get_user_by_telegram_id(message.from_user.id)
    if not user: await message.answer("❌ /start"); return
    if not db.user_has_car_info(user):
        await state.update_data(pending_action='search')
        await message.answer("🚗 <b>Нужны данные авто</b>\n\nГос. номер:",
            reply_markup=get_cancel_menu_keyboard(), parse_mode="HTML")
        await state.set_state(CarInfoStates.waiting_license_plate); return
    await state.update_data(user_id=user['id'])
    slots = db.get_available_slots(None, exclude_supplier=user['id'])
    if not slots:
        await message.answer("😔 Нет доступных мест.", reply_markup=get_no_slots_keyboard(), parse_mode="HTML")
    else:
        await message.answer(
            f"🏠 <b>Доступные места ({len(slots)})</b>",
            reply_markup=get_available_slots_keyboard(slots),
            parse_mode="HTML",
        )
    await state.set_state(SearchStates.selecting_slot)


# CAR INFO
@router.message(CarInfoStates.waiting_license_plate)
async def car_plate(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    ok, r = validate_license_plate(message.text)
    if not ok: await message.answer(r); return
    await state.update_data(license_plate=r)
    await message.answer("🚗 <b>Марка и модель</b>:", parse_mode="HTML")
    await state.set_state(CarInfoStates.waiting_car_brand)

@router.message(CarInfoStates.waiting_car_brand)
async def car_brand(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    ok, r = validate_car_brand(message.text)
    if not ok: await message.answer(r); return
    await state.update_data(car_brand=r)
    await message.answer("🎨 <b>Цвет</b>:", parse_mode="HTML")
    await state.set_state(CarInfoStates.waiting_car_color)

@router.message(CarInfoStates.waiting_car_color)
async def car_color(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    ok, r = validate_car_color(message.text)
    if not ok: await message.answer(r); return
    data = await state.get_data()
    user = db.get_user_by_telegram_id(message.from_user.id)
    db.update_user(user['id'], license_plate=data['license_plate'], car_brand=data['car_brand'], car_color=r)
    pending = data.get('pending_action')
    await state.clear()
    if pending == 'search':
        await state.update_data(user_id=user['id'])
        slots = db.get_available_slots(None, exclude_supplier=user['id'])
        if not slots:
            await message.answer("✅ Авто сохранено!\n\n😔 Нет мест.", reply_markup=get_no_slots_keyboard())
        else:
            await message.answer(f"✅ Авто!\n\n🏠 <b>Места ({len(slots)})</b>\n\n",
                reply_markup=get_available_slots_keyboard(slots), parse_mode="HTML")
        await state.set_state(SearchStates.selecting_slot)
    else:
        await message.answer("✅ Авто обновлено!", reply_markup=get_main_menu_keyboard(_adm(message.from_user.id)))


# SEARCH FILTER
@router.callback_query(F.data == "search_filter")
async def search_filter(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user = db.get_user_by_telegram_id(callback.from_user.id)
    if user: await state.update_data(user_id=user['id'])
    await callback.message.edit_text("📅 <b>Фильтр по дате</b>:",
        reply_markup=get_dates_keyboard("search_date"), parse_mode="HTML")
    await state.set_state(SearchStates.waiting_date)

@router.callback_query(SearchStates.waiting_date, F.data.startswith("search_date_"))
async def search_date(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dv = callback.data.replace("search_date_", "")
    data = await state.get_data()
    uid = data.get('user_id')
    if dv == "manual":
        await callback.message.edit_text("📅 <b>ДД.ММ.ГГГГ</b>:", parse_mode="HTML")
        await state.set_state(SearchStates.waiting_date_manual); return
    if dv == "all":
        slots = db.get_available_slots(None, exclude_supplier=uid)
        if not slots:
            await callback.message.edit_text("😔 Нет мест.", reply_markup=get_no_slots_keyboard())
        else:
            await callback.message.edit_text(f"🏠 <b>Все ({len(slots)})</b>\n\n",
                reply_markup=get_available_slots_keyboard(slots), parse_mode="HTML")
        await state.set_state(SearchStates.selecting_slot); return
    ok, _ = validate_date(dv)
    if not ok: return
    date_obj = datetime.strptime(dv, "%d.%m.%Y")
    slots = db.get_available_slots(date_obj.strftime("%Y-%m-%d"), exclude_supplier=uid)
    if not slots:
        all_s = db.get_available_slots(None, exclude_supplier=uid)
        if all_s:
            await callback.message.edit_text(f"😔 На {dv} нет.\n\n🏠 <b>Все ({len(all_s)})</b>:",
                reply_markup=get_available_slots_keyboard(all_s), parse_mode="HTML")
        else:
            await callback.message.edit_text("😔 Нет мест.", reply_markup=get_no_slots_keyboard())
    else:
        await callback.message.edit_text(f"🏠 <b>На {dv} ({len(slots)})</b>\n\n",
            reply_markup=get_available_slots_keyboard(slots), parse_mode="HTML")
    await state.set_state(SearchStates.selecting_slot)

@router.message(SearchStates.waiting_date_manual)
async def search_date_m(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    ok, _ = validate_date(message.text)
    if not ok: await message.answer("❌ ДД.ММ.ГГГГ"); return
    data = await state.get_data()
    uid = data.get('user_id')
    date_obj = datetime.strptime(message.text, "%d.%m.%Y")
    slots = db.get_available_slots(date_obj.strftime("%Y-%m-%d"), exclude_supplier=uid)
    if not slots:
        all_s = db.get_available_slots(None, exclude_supplier=uid)
        if all_s:
            await message.answer(f"😔 Нет на {message.text}.\n\n🏠 <b>Все ({len(all_s)})</b>:",
                reply_markup=get_available_slots_keyboard(all_s), parse_mode="HTML")
        else: await message.answer("😔 Нет мест.", reply_markup=get_no_slots_keyboard())
    else:
        await message.answer(f"🏠 <b>На {message.text} ({len(slots)})</b>\n\n",
            reply_markup=get_available_slots_keyboard(slots), parse_mode="HTML")
    await state.set_state(SearchStates.selecting_slot)


# ==================== SLOT SELECTION & BOOKING ====================
def _date_range_kb(slot_start, slot_end, prefix):
    buttons = []; dates = []; d = slot_start.date()
    while d <= slot_end.date():
        dates.append(d.strftime("%d.%m.%Y")); d += timedelta(days=1)
    for i in range(0, len(dates), 3):
        buttons.append([InlineKeyboardButton(text=dates[j][:5], callback_data=f"{prefix}_{dates[j]}")
               for j in range(i, min(i+3, len(dates)))])
    buttons.append([InlineKeyboardButton(text="📅 Весь слот", callback_data=f"{prefix}_full")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def _time_range_kb(start_dt, end_dt, prefix, include_end: bool = False):
    buttons = []; times = []; t = start_dt.replace(minute=0, second=0)
    if t < start_dt: t += timedelta(hours=1)
    while t < end_dt or (include_end and t == end_dt):
        times.append(t.strftime("%H:%M")); t += timedelta(hours=1)
    if not times and start_dt < end_dt:
        times.append(start_dt.strftime("%H:%M"))
    for i in range(0, len(times), 3):
        buttons.append([InlineKeyboardButton(text=times[j], callback_data=f"{prefix}_{times[j]}")
               for j in range(i, min(i+3, len(times)))])
    buttons.append([InlineKeyboardButton(text="📅 Весь слот", callback_data=f"{prefix}_full")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def _confirm_text(bs, be):
    h = (be - bs).total_seconds() / 3600
    try:
        tp = calculate_price(bs, be)
    except ValueError:
        tp = "—"
    rate = get_price_per_hour(h)
    return (
        f"📋 <b>Подтверждение</b>\n\n"
        f"📅 {format_datetime(bs)} — {format_datetime(be)}\n"
        f"💰 <b>Итого: {tp}₽</b>\n\n"
        f"🅿️ Номер места будет виден после подтверждения оплаты администратором."
    )


@router.callback_query(SearchStates.selecting_slot, F.data.startswith("slot_"))
async def select_slot(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if await _check_ban(callback): return
    slot_id = int(callback.data.replace("slot_",""))
    slot = db.get_availability_by_id(slot_id)
    if not slot or slot['is_booked']:
        await callback.message.edit_text("❌ Слот уже занят или не найден."); return
    user = db.get_user_by_telegram_id(callback.from_user.id)
    if not user: return
    uid = user['id']
    await state.update_data(user_id=uid)
    if slot['supplier_id'] == uid:
        await callback.message.answer("❌ Нельзя бронировать своё место."); return
    if db.is_blacklisted_either(uid, slot['supplier_id']):
        await callback.message.answer("❌ Бронирование невозможно."); return
    if db.get_active_bookings_count(uid) >= MAX_ACTIVE_BOOKINGS:
        await callback.message.answer(f"❌ Лимит бронирований ({MAX_ACTIVE_BOOKINGS})."); return
    sdt = datetime.fromisoformat(slot['start_time'])
    edt = datetime.fromisoformat(slot['end_time'])
    hours = (edt - sdt).total_seconds() / 3600
    avg_r, cnt_r = db.get_spot_rating(slot['spot_id'])
    rating = f" | ⭐ {avg_r}/5 ({cnt_r})" if cnt_r else ""
    try:
        full_price = calculate_price(sdt, edt)
    except ValueError:
        await callback.message.answer("❌ Этот слот нельзя забронировать по тарифам (ночью минимум 8 часов).")
        return
    rate = get_price_per_hour(hours)
    addr = slot.get('address') or "—"
    await state.update_data(
        selected_slot_id=slot_id, spot_id=slot['spot_id'],
        slot_start=sdt, slot_end=edt,
        spot_number=slot['spot_number'],
        address=addr,
        supplier_telegram_id=slot.get('supplier_telegram_id'),
        supplier_id=slot['supplier_id'],
    )
    header = (
        f"📍 <b>{addr}</b>{rating}\n"
        f"📅 {format_datetime(sdt)} — {format_datetime(edt)}\n"
        f"🅿️ Номер места будет виден после подтверждения оплаты.\n\n"
    )
    multi_day = sdt.date() != edt.date()
    if multi_day:
        await callback.message.edit_text(header + "📅 <b>Дата начала</b>:",
            reply_markup=_date_range_kb(sdt, edt, "bksd"), parse_mode="HTML")
        await state.set_state(SearchStates.selecting_start_date)
    elif hours > 1:
        await callback.message.edit_text(header + "⏰ <b>Время начала</b>:",
            reply_markup=_time_range_kb(sdt, edt, "bkst"), parse_mode="HTML")
        await state.set_state(SearchStates.selecting_start_time)
    else:
        try:
            tp = calculate_price(sdt, edt)
        except ValueError:
            await callback.message.answer("❌ Ночью можно бронировать минимум 8 часов (если бронь только ночью).")
            return
        await state.update_data(start_time=sdt, end_time=edt, total_price=tp)
        await callback.message.edit_text(_confirm_text(sdt, edt),
            reply_markup=get_confirm_keyboard("booking_confirm"), parse_mode="HTML")
        await state.set_state(SearchStates.confirming_booking)

# Booking: Start Date
@router.callback_query(SearchStates.selecting_start_date, F.data.startswith("bksd_"))
async def bk_start_date(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    val = callback.data.replace("bksd_","")
    data = await state.get_data()
    sdt, edt = data['slot_start'], data['slot_end']
    if val == "full":
        try:
            tp = calculate_price(sdt, edt)
        except ValueError:
            await callback.message.answer("❌ Ночью можно бронировать минимум 8 часов (если бронь только ночью).")
            return
        await state.update_data(start_time=sdt, end_time=edt, total_price=tp)
        await callback.message.edit_text(_confirm_text(sdt, edt),
            reply_markup=get_confirm_keyboard("booking_confirm"), parse_mode="HTML")
        await state.set_state(SearchStates.confirming_booking); return
    try: picked = datetime.strptime(val, "%d.%m.%Y").date()
    except: return
    await state.update_data(booking_start_date=picked)
    t_from = sdt if picked == sdt.date() else datetime.combine(picked, datetime.min.time())
    t_to = edt if picked == edt.date() else datetime.combine(picked, datetime.max.time().replace(microsecond=0))
    await callback.message.edit_text(f"📅 {picked.strftime('%d.%m.%Y')}\n\n⏰ <b>Время начала</b>:",
        reply_markup=_time_range_kb(t_from, t_to, "bkst"), parse_mode="HTML")
    await state.set_state(SearchStates.selecting_start_time)

# Booking: Start Time
@router.callback_query(SearchStates.selecting_start_time, F.data.startswith("bkst_"))
async def bk_start_time(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    val = callback.data.replace("bkst_","")
    data = await state.get_data()
    sdt, edt = data['slot_start'], data['slot_end']
    if val == "full":
        try:
            tp = calculate_price(sdt, edt)
        except ValueError:
            await callback.message.answer("❌ Ночью можно бронировать минимум 8 часов (если бронь только ночью).")
            return
        await state.update_data(start_time=sdt, end_time=edt, total_price=tp)
        await callback.message.edit_text(_confirm_text(sdt, edt),
            reply_markup=get_confirm_keyboard("booking_confirm"), parse_mode="HTML")
        await state.set_state(SearchStates.confirming_booking); return
    try:
        t = datetime.strptime(val, "%H:%M").time()
        sd = data.get('booking_start_date', sdt.date())
        bs = datetime.combine(sd, t)
        if bs < sdt: bs = sdt
        if bs >= edt: return
    except: return
    await state.update_data(booking_start=bs)
    if bs.date() != edt.date():
        await callback.message.edit_text(f"📅 Начало: <b>{format_datetime(bs)}</b>\n\n📅 <b>Дата окончания</b>:",
            reply_markup=_date_range_kb(bs, edt, "bked"), parse_mode="HTML")
        await state.set_state(SearchStates.selecting_end_date)
    else:
        await callback.message.edit_text(f"📅 Начало: <b>{format_datetime(bs)}</b>\n\n⏰ <b>Время окончания</b>:",
            reply_markup=_time_range_kb(bs + timedelta(hours=1), edt, "bket", include_end=True), parse_mode="HTML")
        await state.set_state(SearchStates.selecting_end_time)

# Booking: End Date
@router.callback_query(SearchStates.selecting_end_date, F.data.startswith("bked_"))
async def bk_end_date(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    val = callback.data.replace("bked_","")
    data = await state.get_data()
    bs = data['booking_start']; edt = data['slot_end']
    if val == "full":
        try:
            tp = calculate_price(bs, edt)
        except ValueError:
            await callback.message.answer("❌ Ночью можно бронировать минимум 8 часов (если бронь только ночью).")
            return
        await state.update_data(start_time=bs, end_time=edt, total_price=tp)
        await callback.message.edit_text(_confirm_text(bs, edt),
            reply_markup=get_confirm_keyboard("booking_confirm"), parse_mode="HTML")
        await state.set_state(SearchStates.confirming_booking); return
    try: picked = datetime.strptime(val, "%d.%m.%Y").date()
    except: return
    await state.update_data(booking_end_date=picked)
    t_from = bs + timedelta(hours=1) if picked == bs.date() else datetime.combine(picked, datetime.min.time().replace(hour=1))
    t_to = edt if picked == edt.date() else datetime.combine(picked, datetime.max.time().replace(hour=23, minute=0, second=0, microsecond=0))
    await callback.message.edit_text(f"📅 {format_datetime(bs)} — <b>{picked.strftime('%d.%m.%Y')}</b>\n\n⏰ <b>Время окончания</b>:",
        reply_markup=_time_range_kb(t_from, t_to, "bket", include_end=True), parse_mode="HTML")
    await state.set_state(SearchStates.selecting_end_time)

# Booking: End Time
@router.callback_query(SearchStates.selecting_end_time, F.data.startswith("bket_"))
async def bk_end_time(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    val = callback.data.replace("bket_","")
    data = await state.get_data()
    bs = data['booking_start']; edt = data['slot_end']
    if val == "full": be = edt
    else:
        try:
            t = datetime.strptime(val, "%H:%M").time()
            ed = data.get('booking_end_date', bs.date())
            be = datetime.combine(ed, t)
            if be <= bs or be > edt: return
        except: return
    try:
        tp = calculate_price(bs, be)
    except ValueError:
        await callback.message.answer("❌ Ночью можно бронировать минимум 8 часов (если бронь только ночью).")
        return
    await state.update_data(start_time=bs, end_time=be, total_price=tp)
    await callback.message.edit_text(_confirm_text(bs, be),
        reply_markup=get_confirm_keyboard("booking_confirm"), parse_mode="HTML")
    await state.set_state(SearchStates.confirming_booking)


# Booking: Confirm → заявка (pending) → админу
@router.callback_query(SearchStates.confirming_booking, F.data.startswith("booking_confirm_"))
async def confirm_booking(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data == "booking_confirm_no":
        await state.clear()
        await callback.message.edit_text("❌ Отменено.")
        await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id))); return
    data = await state.get_data()
    needed = ('user_id','spot_id','selected_slot_id','start_time','end_time','total_price')
    if not all(k in data for k in needed):
        await state.clear(); await callback.message.edit_text("❌ Данные потеряны."); return
    try:
        bid = db.create_booking(data['user_id'], data['spot_id'], data['selected_slot_id'],
                                data['start_time'], data['end_time'], data['total_price'])
    except Exception as e:
        logger.error(f"Booking: {e}")
        msg = str(e).lower()
        if "past" in msg or "прошл" in msg:
            text = "❌ Нельзя бронировать время в прошлом."
        elif "night booking" in msg or "at least 8" in msg:
            text = "❌ Ночью можно бронировать минимум 8 часов (если бронь только ночью)."
        elif "outside" in msg or "вне" in msg:
            text = "❌ Вы выбрали время вне доступного слота."
        elif "booked" in msg or "занят" in msg:
            text = "❌ Слот уже занят."
        else:
            text = "❌ Ошибка бронирования."
        await state.clear()
        await callback.message.edit_text(text)
        return
    user = db.get_user_by_telegram_id(callback.from_user.id)
    await state.clear()
    h = (data['end_time'] - data['start_time']).total_seconds() / 3600
    rate = get_price_per_hour(h)
    supplier = db.get_user_by_id(data.get('supplier_id')) if data.get('supplier_id') else None
    card_number = ""
    bank_name = ""
    if supplier and supplier.get('card_number'):
        card_number = supplier.get('card_number')
        bank_name = supplier.get('bank', '')
    elif CARD_NUMBER:
        card_number = CARD_NUMBER
    # Для удобного копирования показываем карту моноширинным кодом.
    def _card_display(n: str) -> str:
        # Только цифры без пробелов — так лучше копируется.
        n = re.sub(r"\D", "", (n or ""))
        return n

    card_line = ""
    if card_number:
        shown = _card_display(card_number)
        card_line = f"\n\n💳 {bank_name + ': ' if bank_name else ''}<pre><code>{shown}</code></pre>"

    pay_instruction = (
        f"Переведите сумму на карту выше и отправьте чек администратору {ADMIN_CHECK_USERNAME}."
        if card_number
        else f"Реквизиты для оплаты уточните у администратора {ADMIN_CHECK_USERNAME}, затем отправьте чек."
    )

    await callback.message.edit_text(
        f"✅ <b>Заявка #{bid} создана!</b>\n\n"
        f"🅿️ Номер места будет виден после подтверждения оплаты администратором.\n"
        f"📍 {FIXED_ADDRESS}\n"
        f"📅 {format_datetime(data['start_time'])} — {format_datetime(data['end_time'])}\n"
        f"💰 <b>{data['total_price']}₽</b>"
        f"{card_line}\n\n"
        f"{pay_instruction}\n"
        f"После оплаты нажмите «✅ Я оплатил».",
        reply_markup=booking_payment_keyboard(bid),
        parse_mode="HTML"
    )

    # Отдельным сообщением — чтобы было максимально удобно копировать реквизиты.
    if card_number:
        try:
            shown = _card_display(card_number)
            bank_prefix = f"{bank_name}:\n" if bank_name else ""
            await callback.message.answer(
                f"💳 <b>Карта для оплаты</b>\n{bank_prefix}<pre><code>{shown}</code></pre>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))
    # Админам
    try:
        car = ""
        if user and user.get('license_plate'):
            car = f"\n🚗 {user['car_brand']} {user['car_color']} ({user['license_plate']})"
        cust_info = f"👤 {user['full_name']}\n📞 {user['phone']}"
        if user.get('username'): cust_info += f"\n📱 @{user['username']}"
        supplier = db.get_user_by_id(data.get('supplier_id'))
        await callback.bot.send_message(data.get('supplier_telegram_id'),
            f"📋 <b>Новая заявка #{bid}!</b>\n🏠 {data.get('spot_number','')}\n"
            f"📅 {format_datetime(data['start_time'])} — {format_datetime(data['end_time'])}\n"
            f"⏳ Ожидает подтверждения.", parse_mode="HTML")
    except: pass


# ==================== ADD SPOT — запоминаем места ====================
@router.message(F.text == "➕ Добавить место")
async def add_spot_start(message: Message, state: FSMContext):
    if await _check_ban(message): return
    user = db.get_user_by_telegram_id(message.from_user.id)
    if not user: await message.answer("❌ /start"); return
    if not db.user_has_card_info(user):
        await state.update_data(pending_action='add_spot', supplier_id=user['id'])
        await message.answer("💳 <b>Нужна карта</b>\n\n16 цифр:",
            reply_markup=get_cancel_menu_keyboard(), parse_mode="HTML")
        await state.set_state(CardInfoStates.waiting_card); return
    # Если есть места — показать их + кнопку "Новое место"
    existing = db.get_user_spots(user['id'])
    await state.update_data(supplier_id=user['id'])
    if existing:
        buttons = []
        for sp in existing:
            buttons.append([InlineKeyboardButton(text=f"🏠 {sp['spot_number']} — добавить слот",
                callback_data=f"addslot_{sp['id']}")])
        if len(existing) < MAX_SPOTS_PER_USER:
            buttons.append([InlineKeyboardButton(text="➕ Новое место", callback_data="new_spot")])
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
        await message.answer("🏠 <b>Ваши места:</b>\nВыберите для добавления слота или создайте новое:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    else:
        # Важно: состояние должно быть waiting_spot_number,
        # иначе следующий ввод номера места не поймается хендлером.
        await message.answer(
            "📍 <b>Введите номер места</b>:",
            reply_markup=get_cancel_menu_keyboard(),
            parse_mode="HTML",
        )
        await state.set_state(AddSpotStates.waiting_spot_number)

@router.callback_query(F.data == "new_spot")
async def new_spot(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("📍 <b>Введите номер места</b>:",  parse_mode="HTML")
    await state.set_state(AddSpotStates.waiting_spot_number)

# CARD INFO
@router.callback_query(CardInfoStates.waiting_bank, F.data.startswith("bank_"))
async def card_bank(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bank = callback.data.replace("bank_","")
    if bank == "Другой":
        await callback.message.edit_text("🏦 Введите название банка:")
        await state.set_state(CardInfoStates.waiting_bank_name); return
    data = await state.get_data()
    user = db.get_user_by_telegram_id(callback.from_user.id)
    db.update_user(user['id'], card_number=data['card_number'], bank=bank)
    pending = data.get('pending_action')
    await state.clear()
    if pending == 'add_spot':
        await state.update_data(supplier_id=user['id'])
        await callback.message.edit_text(f"✅ Карта: {bank}")
        await callback.message.answer("🏠 <b>Номер места</b>:", reply_markup=get_cancel_menu_keyboard(), parse_mode="HTML")
        await state.set_state(AddSpotStates.waiting_spot_number)
    else:
        await callback.message.edit_text(f"✅ Карта: {bank}")
        await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))

@router.message(CardInfoStates.waiting_bank_name)
async def card_bank_manual(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    bank = message.text.strip()
    if len(bank) < 2 or len(bank) > 30: await message.answer("❌ 2-30 символов"); return
    data = await state.get_data()
    user = db.get_user_by_telegram_id(message.from_user.id)
    db.update_user(user['id'], card_number=data['card_number'], bank=bank)
    pending = data.get('pending_action')
    await state.clear()
    if pending == 'add_spot':
        await state.update_data(supplier_id=user['id'])
        await message.answer(f"✅ Карта: {bank}\n\n🏠 <b>Номер места</b>:",
            reply_markup=get_cancel_menu_keyboard(), parse_mode="HTML")
        await state.set_state(AddSpotStates.waiting_spot_number)
    else:
        await message.answer(f"✅ Карта: {bank}", reply_markup=get_main_menu_keyboard(_adm(message.from_user.id)))

@router.message(CardInfoStates.waiting_card)
async def card_number(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    ok, r = validate_card(message.text)
    if not ok: await message.answer(r); return
    await state.update_data(card_number=r)
    await message.answer("🏦 Банк:", reply_markup=get_bank_keyboard(), parse_mode="HTML")
    await state.set_state(CardInfoStates.waiting_bank)


# SPOT: номер → даты → подтверждение



@router.message(AddSpotStates.waiting_spot_number)
async def sp_num(message: Message, state: FSMContext):
    if _cancel_check(message.text):
        await cancel_msg(message, state)
        return
    ok, r = validate_spot_number(message.text)
    if not ok:
        await message.answer(r)
        return
    await state.update_data(spot_number=r)
    await message.answer("📅 <b>Выберите дату начала</b>:", reply_markup=get_dates_keyboard("start_date"), parse_mode="HTML")
    await state.set_state(AddSpotStates.waiting_start_date)

@router.callback_query(AddSpotStates.waiting_start_date, F.data.startswith("start_date_"))
async def sp_sd(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dv = callback.data.replace("start_date_","")
    if dv == "manual":
        await callback.message.edit_text("📅 ДД.ММ.ГГГГ:"); await state.set_state(AddSpotStates.waiting_start_date_manual); return
    if dv == "all": return
    ok, _ = validate_date(dv)
    if not ok: return
    await state.update_data(start_date=dv)
    await callback.message.edit_text("⏰ Время начала:", reply_markup=get_time_slots_keyboard("start_time", _min_dt_for_date(dv)))
    await state.set_state(AddSpotStates.waiting_start_time)

@router.message(AddSpotStates.waiting_start_date_manual)
async def sp_sd_m(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    ok, _ = validate_date(message.text)
    if not ok: await message.answer("❌ ДД.ММ.ГГГГ"); return
    await state.update_data(start_date=message.text)
    await message.answer("⏰ Время начала:", reply_markup=get_time_slots_keyboard("start_time", _min_dt_for_date(message.text)))
    await state.set_state(AddSpotStates.waiting_start_time)

@router.callback_query(AddSpotStates.waiting_start_time, F.data.startswith("start_time_"))
async def sp_st(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tv = callback.data.replace("start_time_","")
    if tv == "manual":
        await callback.message.edit_text("⏰ ЧЧ:ММ:"); await state.set_state(AddSpotStates.waiting_start_time_manual); return
    await state.update_data(start_time_str=tv)
    await callback.message.edit_text("📅 <b>Дата окончания</b>:", reply_markup=get_dates_keyboard("end_date"), parse_mode="HTML")
    await state.set_state(AddSpotStates.waiting_end_date)

@router.message(AddSpotStates.waiting_start_time_manual)
async def sp_st_m(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    ok, r = validate_time(message.text)
    if not ok: await message.answer("❌ ЧЧ:ММ"); return
    await state.update_data(start_time_str=r)
    await message.answer("📅 <b>Дата окончания</b>:", reply_markup=get_dates_keyboard("end_date"), parse_mode="HTML")
    await state.set_state(AddSpotStates.waiting_end_date)

@router.callback_query(AddSpotStates.waiting_end_date, F.data.startswith("end_date_"))
async def sp_ed(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dv = callback.data.replace("end_date_","")
    if dv == "manual":
        await callback.message.edit_text("📅 ДД.ММ.ГГГГ:"); await state.set_state(AddSpotStates.waiting_end_date_manual); return
    if dv == "all": return
    data = await state.get_data()
    ok, pe = validate_date(dv); _, ps = validate_date(data['start_date'])
    if not ok or pe < ps: return
    await state.update_data(end_date=dv)
    await callback.message.edit_text("⏰ Время окончания:", reply_markup=get_time_slots_keyboard("end_time", _min_dt_for_date(dv)))
    await state.set_state(AddSpotStates.waiting_end_time)

@router.message(AddSpotStates.waiting_end_date_manual)
async def sp_ed_m(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    data = await state.get_data()
    ok, pe = validate_date(message.text); _, ps = validate_date(data['start_date'])
    if not ok or pe < ps: await message.answer("❌"); return
    await state.update_data(end_date=message.text)
    await message.answer("⏰ Время окончания:", reply_markup=get_time_slots_keyboard("end_time", _min_dt_for_date(message.text)))
    await state.set_state(AddSpotStates.waiting_end_time)

@router.callback_query(AddSpotStates.waiting_end_time, F.data.startswith("end_time_"))
async def sp_et(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tv = callback.data.replace("end_time_","")
    if tv == "manual":
        await callback.message.edit_text("⏰ ЧЧ:ММ:"); await state.set_state(AddSpotStates.waiting_end_time_manual); return
    data = await state.get_data()
    sdt = parse_datetime(data['start_date'], data['start_time_str'])
    edt = parse_datetime(data['end_date'], tv)
    if not edt or edt <= sdt: return
    await state.update_data(end_time_str=tv)
    await callback.message.edit_text(
        f"📋 <b>Проверьте:</b>\n\n🏠 {data['spot_number']}\n"
        f"📅 {data['start_date']} {data['start_time_str']} — {data['end_date']} {tv}",
        reply_markup=get_confirm_keyboard("spot_confirm"), parse_mode="HTML")
    await state.set_state(AddSpotStates.confirming)

@router.message(AddSpotStates.waiting_end_time_manual)
async def sp_et_m(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    ok, r = validate_time(message.text)
    if not ok: await message.answer("❌ ЧЧ:ММ"); return
    data = await state.get_data()
    sdt = parse_datetime(data['start_date'], data['start_time_str'])
    edt = parse_datetime(data['end_date'], r)
    if not edt or edt <= sdt: await message.answer("❌ Позже начала"); return
    await state.update_data(end_time_str=r)
    await message.answer(
        f"📋 <b>Проверьте:</b>\n\n🏠 {data['spot_number']}\n"
        f"📅 {data['start_date']} {data['start_time_str']} — {data['end_date']} {r}",
        reply_markup=get_confirm_keyboard("spot_confirm"), parse_mode="HTML")
    await state.set_state(AddSpotStates.confirming)

@router.callback_query(AddSpotStates.confirming, F.data.startswith("spot_confirm_"))
async def spot_confirm(callback: CallbackQuery, state: FSMContext):
    # Always answer callback immediately so Telegram button never spins forever
    await callback.answer()

    if callback.data == "spot_confirm_no":
        await state.clear()
        await callback.message.edit_text("❌ Отменено.")
        await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))
        return

    # YES
    try:
        await callback.message.edit_text("⏳ Создаю слот...")
    except Exception:
        pass

    data = await state.get_data()
    try:
        sdt = parse_datetime(data['start_date'], data['start_time_str'])
        edt = parse_datetime(data['end_date'], data['end_time_str'])

        if not sdt or not edt or edt <= sdt:
            await callback.message.edit_text("❌ Неверный интервал.")
            await state.clear()
            return

        # Normalize to step before checks/DB
        sdt = round_to_step(sdt, TIME_STEP_MINUTES)
        edt = round_to_step(edt, TIME_STEP_MINUTES)

        # No past slots
        if sdt < now_local():
            await callback.message.edit_text("❌ Нельзя создавать слот в прошлом.")
            await state.clear()
            return

        # validate_interval ожидает datetimes в одном формате (naive).
        # Используем now_local(), иначе на некоторых хостингах ловим
        # "can't compare offset-naive and offset-aware datetimes".
        ok, msg = validate_interval(
            sdt, edt,
            now_local(),
            MIN_BOOKING_MINUTES,
            WORKING_HOURS_START,
            WORKING_HOURS_END
        )
        if not ok:
            await callback.message.edit_text(msg)
            await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))
            await state.clear()
            return

        # Save spot (remember place)
        spot_id = db.get_or_create_spot(data['supplier_id'], data['spot_number'], address=FIXED_ADDRESS)

        # Overlap check
        if db.check_slot_overlap(spot_id, sdt, edt):
            await callback.message.edit_text("❌ Слот пересекается с существующим!")
            await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))
            await state.clear()
            return

        db.create_spot_availability(spot_id, sdt, edt)

        await state.clear()
        await callback.message.edit_text(
            f"✅ <b>Слот добавлен!</b>\n\n🏠 {data['spot_number']}\n"
            f"📅 {format_datetime(sdt)} — {format_datetime(edt)}",
            parse_mode="HTML"
        )
        await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))

        # Notify subscribers (optional)
        for n in db.get_matching_notifications(spot_id, sdt, edt):
            try:
                await callback.bot.send_message(n['telegram_id'], f"🔔 Место {data['spot_number']} освободилось!")
                db.deactivate_notification(n['id'])
            except Exception:
                pass

    except Exception as e:
        try:
            await callback.message.edit_text(f"❌ Ошибка при создании слота: {e}")
        except Exception:
            pass
        await state.clear()
        return
@router.message(F.text == "🏠 Мои слоты")
async def my_spots(message: Message, state: FSMContext):
    user = db.get_user_by_telegram_id(message.from_user.id)
    if not user: await message.answer("❌ /start"); return
    spots = db.get_user_spots(user['id'])
    if not spots:
        await message.answer("😔 У вас нет мест.\nДобавьте через «➕ Добавить место»"); return
    await message.answer("🏠 <b>Ваши места:</b>", reply_markup=get_my_spots_keyboard(spots), parse_mode="HTML")

@router.callback_query(F.data.startswith("myspot_"))
async def spot_detail(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    sid = int(callback.data.replace("myspot_",""))
    spot = db.get_spot_by_id(sid)
    if not spot: await callback.message.edit_text("❌ Не найдено."); return
    await state.update_data(current_spot_id=sid)
    avails = db.get_spot_availabilities(sid)
    at = ""
    for a in avails:
        s = datetime.fromisoformat(a['start_time'])
        e = datetime.fromisoformat(a['end_time'])
        at += f"\n📅 {format_datetime(s)} — {format_datetime(e)}"
    if not at: at = "\nНет активных слотов"
    # Кнопки: слоты (кликабельные для свободных) + добавить/удалить
    buttons = []
    for a in avails:
        if not a['is_booked']:
            s = datetime.fromisoformat(a['start_time'])
            e = datetime.fromisoformat(a['end_time'])
            buttons.append([InlineKeyboardButton(
                text=f"✏️ {s.strftime('%d.%m %H:%M')}-{e.strftime('%d.%m %H:%M')}",
                callback_data=f"myslot_{a['id']}")])
    buttons.append([InlineKeyboardButton(text="📅 Добавить слот", callback_data=f"addslot_{sid}")])
    buttons.append([InlineKeyboardButton(text="❌ Удалить место", callback_data=f"delspot_{sid}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_spots")])
    await callback.message.edit_text(
        f"🏠 <b>{spot['spot_number']}</b>\n📅 Слоты:{at}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

# Нажали на свободный слот — действия
@router.callback_query(F.data.startswith("myslot_"))
async def myslot_actions(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    aid = int(callback.data.replace("myslot_",""))
    slot = db.get_slot_by_id(aid)
    if not slot or slot['is_booked']:
        await callback.message.edit_text("❌ Слот занят или не найден."); return
    s = datetime.fromisoformat(slot['start_time'])
    e = datetime.fromisoformat(slot['end_time'])
    await state.update_data(edit_slot_id=aid, edit_slot_spot_id=slot['spot_id'])
    await callback.message.edit_text(
        f"📅 {format_datetime(s)} — {format_datetime(e)}\n🟢 Свободен",
        reply_markup=get_slot_actions_keyboard(aid, False))

# Удалить слот
@router.callback_query(F.data.startswith("delslot_"))
async def del_slot(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    aid = int(callback.data.replace("delslot_",""))
    ok = db.delete_slot(aid)
    if ok: await callback.message.edit_text("✅ Слот удалён.")
    else: await callback.message.edit_text("❌ Не удалось удалить (возможно забронирован).")
    await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))

# Редактировать слот — выбор что менять
@router.callback_query(F.data.startswith("editslot_"))
async def edit_slot_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    aid = int(callback.data.replace("editslot_",""))
    slot = db.get_slot_by_id(aid)
    if not slot or slot['is_booked']:
        await callback.message.edit_text("❌ Слот занят."); return
    await state.update_data(edit_slot_id=aid, edit_slot_spot_id=slot['spot_id'],
                            edit_orig_start=slot['start_time'], edit_orig_end=slot['end_time'])
    s = datetime.fromisoformat(slot['start_time'])
    e = datetime.fromisoformat(slot['end_time'])
    await callback.message.edit_text(
        f"✏️ <b>Редактирование слота</b>\n"
        f"📅 {format_datetime(s)} — {format_datetime(e)}\n\n"
        f"Что изменить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Начало", callback_data="es_start"),
             InlineKeyboardButton(text="📅 Конец", callback_data="es_end")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
        ]), parse_mode="HTML")
    await state.set_state(EditSlotStates.choosing_field)

@router.callback_query(EditSlotStates.choosing_field, F.data == "es_start")
async def es_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("📅 <b>Новая дата начала</b>:", reply_markup=get_dates_keyboard("es_sd"), parse_mode="HTML")
    await state.set_state(EditSlotStates.waiting_start_date)

@router.callback_query(EditSlotStates.waiting_start_date, F.data.startswith("es_sd_"))
async def es_sd(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dv = callback.data.replace("es_sd_","")
    if dv in ("manual", "all"):
        await callback.answer("Выберите дату кнопкой.", show_alert=True)
        return
    ok, _ = validate_date(dv)
    if not ok:
        return
    await state.update_data(es_new_start_date=dv)
    await callback.message.edit_text("⏰ <b>Новое время начала</b>:", reply_markup=get_time_slots_keyboard("es_st", _min_dt_for_date(dv)), parse_mode="HTML")
    await state.set_state(EditSlotStates.waiting_start_time)

@router.callback_query(EditSlotStates.waiting_start_time, F.data.startswith("es_st_"))
async def es_st(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tv = callback.data.replace("es_st_","")
    if tv == "manual":
        await callback.answer("Выберите время кнопкой.", show_alert=True)
        return
    ok, r = validate_time(tv)
    if not ok:
        await callback.answer("Только ЧЧ:00", show_alert=True)
        return
    data = await state.get_data()
    new_start = parse_datetime(data['es_new_start_date'], r)
    old_end = datetime.fromisoformat(data['edit_orig_end'])
    if not new_start or new_start >= old_end:
        await callback.answer("Начало должно быть раньше конца.", show_alert=True)
        return
    aid = data['edit_slot_id']; spot_id = data['edit_slot_spot_id']
    if db.check_slot_overlap(spot_id, new_start, old_end, exclude_slot_id=aid):
        await callback.message.edit_text("❌ Пересечение с другим слотом!")
        await state.clear()
        return
    db.update_slot_times(aid, new_start, old_end)
    await state.clear()
    await callback.message.edit_text(f"✅ Слот обновлён!\n📅 {format_datetime(new_start)} — {format_datetime(old_end)}")
    await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))

@router.message(EditSlotStates.waiting_start_date)
async def es_start_date(message: Message, state: FSMContext):
    await message.answer("ℹ️ Используйте кнопки для выбора даты/времени.", reply_markup=get_cancel_keyboard())

@router.message(EditSlotStates.waiting_start_time)
async def es_start_time(message: Message, state: FSMContext):
    await message.answer("ℹ️ Используйте кнопки для выбора даты/времени.", reply_markup=get_cancel_keyboard())

@router.callback_query(EditSlotStates.choosing_field, F.data == "es_end")
async def es_end(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("📅 <b>Новая дата окончания</b>:", reply_markup=get_dates_keyboard("es_ed"), parse_mode="HTML")
    await state.set_state(EditSlotStates.waiting_end_date)

@router.callback_query(EditSlotStates.waiting_end_date, F.data.startswith("es_ed_"))
async def es_ed(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dv = callback.data.replace("es_ed_","")
    if dv in ("manual", "all"):
        await callback.answer("Выберите дату кнопкой.", show_alert=True)
        return
    ok, _ = validate_date(dv)
    if not ok:
        return
    await state.update_data(es_new_end_date=dv)
    await callback.message.edit_text("⏰ <b>Новое время окончания</b>:", reply_markup=get_time_slots_keyboard("es_et", _min_dt_for_date(dv)), parse_mode="HTML")
    await state.set_state(EditSlotStates.waiting_end_time)

@router.callback_query(EditSlotStates.waiting_end_time, F.data.startswith("es_et_"))
async def es_et(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tv = callback.data.replace("es_et_","")
    if tv == "manual":
        await callback.answer("Выберите время кнопкой.", show_alert=True)
        return
    ok, r = validate_time(tv)
    if not ok:
        await callback.answer("Только ЧЧ:00", show_alert=True)
        return
    data = await state.get_data()
    old_start = datetime.fromisoformat(data['edit_orig_start'])
    new_end = parse_datetime(data['es_new_end_date'], r)
    if not new_end or new_end <= old_start:
        await callback.answer("Конец должен быть позже начала.", show_alert=True)
        return
    aid = data['edit_slot_id']; spot_id = data['edit_slot_spot_id']
    if db.check_slot_overlap(spot_id, old_start, new_end, exclude_slot_id=aid):
        await callback.message.edit_text("❌ Пересечение с другим слотом!")
        await state.clear()
        return
    db.update_slot_times(aid, old_start, new_end)
    await state.clear()
    await callback.message.edit_text(f"✅ Слот обновлён!\n📅 {format_datetime(old_start)} — {format_datetime(new_end)}")
    await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))

@router.message(EditSlotStates.waiting_end_date)
async def es_end_date(message: Message, state: FSMContext):
    await message.answer("ℹ️ Используйте кнопки для выбора даты/времени.", reply_markup=get_cancel_keyboard())

@router.message(EditSlotStates.waiting_end_time)
async def es_end_time(message: Message, state: FSMContext):
    await message.answer("ℹ️ Используйте кнопки для выбора даты/времени.", reply_markup=get_cancel_keyboard())

@router.callback_query(F.data == "back_spot_detail")
async def back_spot_detail(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    sid = data.get('current_spot_id') or data.get('edit_slot_spot_id')
    if not sid:
        await callback.message.edit_text("🔙"); return
    spot = db.get_spot_by_id(sid)
    if not spot: return
    avails = db.get_spot_availabilities(sid)
    buttons = []
    for a in avails:
        if not a['is_booked']:
            s = datetime.fromisoformat(a['start_time'])
            e = datetime.fromisoformat(a['end_time'])
            buttons.append([InlineKeyboardButton(
                text=f"✏️ {s.strftime('%d.%m %H:%M')}-{e.strftime('%d.%m %H:%M')}",
                callback_data=f"myslot_{a['id']}")])
    buttons.append([InlineKeyboardButton(text="📅 Добавить слот", callback_data=f"addslot_{sid}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_spots")])
    at = ""
    for a in avails:
        s = datetime.fromisoformat(a['start_time'])
        e = datetime.fromisoformat(a['end_time'])
        status = "🔴" if a['is_booked'] else "🟢"
        at += f"\n{status} {format_datetime(s)} — {format_datetime(e)}"
    if not at: at = "\nНет слотов"
    await callback.message.edit_text(f"🏠 <b>{spot['spot_number']}</b>{at}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data == "back_spots")
async def back_spots(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user = db.get_user_by_telegram_id(callback.from_user.id)
    spots = db.get_user_spots(user['id'])
    if not spots: await callback.message.edit_text("😔 Нет мест.")
    else: await callback.message.edit_text("🏠 <b>Ваши места:</b>",
        reply_markup=get_my_spots_keyboard(spots), parse_mode="HTML")

# Добавить слот к существующему месту
@router.callback_query(F.data.startswith("addslot_"))
async def addslot(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    sid = int(callback.data.replace("addslot_",""))
    await state.update_data(addslot_spot_id=sid)
    await callback.message.edit_text("📅 <b>Дата начала</b>:", reply_markup=get_dates_keyboard("aslot_sd"), parse_mode="HTML")
    await state.set_state(AddSlotStates.waiting_start_date)

@router.callback_query(AddSlotStates.waiting_start_date, F.data.startswith("aslot_sd_"))
async def aslot_sd(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dv = callback.data.replace("aslot_sd_","")
    if dv == "manual":
        await callback.message.edit_text("📅 ДД.ММ.ГГГГ:"); await state.set_state(AddSlotStates.waiting_start_date_manual); return
    if dv == "all": return
    ok, _ = validate_date(dv)
    if not ok: return
    await state.update_data(aslot_start_date=dv)
    await callback.message.edit_text("⏰ Время начала:", reply_markup=get_time_slots_keyboard("aslot_st", _min_dt_for_date(dv)))
    await state.set_state(AddSlotStates.waiting_start_time)

@router.message(AddSlotStates.waiting_start_date_manual)
async def aslot_sd_m(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    ok, _ = validate_date(message.text)
    if not ok: await message.answer("❌"); return
    await state.update_data(aslot_start_date=message.text)
    await message.answer("⏰ Время начала:", reply_markup=get_time_slots_keyboard("aslot_st", _min_dt_for_date(message.text)))
    await state.set_state(AddSlotStates.waiting_start_time)

@router.callback_query(AddSlotStates.waiting_start_time, F.data.startswith("aslot_st_"))
async def aslot_st(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tv = callback.data.replace("aslot_st_","")
    if tv == "manual":
        await callback.message.edit_text("⏰ ЧЧ:ММ:"); await state.set_state(AddSlotStates.waiting_start_time_manual); return
    await state.update_data(aslot_start_time=tv)
    await callback.message.edit_text("📅 <b>Дата окончания</b>:", reply_markup=get_dates_keyboard("aslot_ed"), parse_mode="HTML")
    await state.set_state(AddSlotStates.waiting_end_date)

@router.message(AddSlotStates.waiting_start_time_manual)
async def aslot_st_m(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    ok, r = validate_time(message.text)
    if not ok: await message.answer("❌"); return
    await state.update_data(aslot_start_time=r)
    await message.answer("📅 Дата окончания:", reply_markup=get_dates_keyboard("aslot_ed"))
    await state.set_state(AddSlotStates.waiting_end_date)

@router.callback_query(AddSlotStates.waiting_end_date, F.data.startswith("aslot_ed_"))
async def aslot_ed(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dv = callback.data.replace("aslot_ed_","")
    if dv == "manual":
        await callback.message.edit_text("📅 ДД.ММ.ГГГГ:"); await state.set_state(AddSlotStates.waiting_end_date_manual); return
    if dv == "all": return
    data = await state.get_data()
    ok, pe = validate_date(dv); _, ps = validate_date(data['aslot_start_date'])
    if not ok or pe < ps: return
    await state.update_data(aslot_end_date=dv)
    await callback.message.edit_text("⏰ Время окончания:", reply_markup=get_time_slots_keyboard("aslot_et", _min_dt_for_date(dv)))
    await state.set_state(AddSlotStates.waiting_end_time)

@router.message(AddSlotStates.waiting_end_date_manual)
async def aslot_ed_m(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    data = await state.get_data()
    ok, pe = validate_date(message.text); _, ps = validate_date(data['aslot_start_date'])
    if not ok or pe < ps: await message.answer("❌"); return
    await state.update_data(aslot_end_date=message.text)
    await message.answer("⏰ Время окончания:", reply_markup=get_time_slots_keyboard("aslot_et", _min_dt_for_date(message.text)))
    await state.set_state(AddSlotStates.waiting_end_time)

@router.callback_query(AddSlotStates.waiting_end_time, F.data.startswith("aslot_et_"))
async def aslot_et(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tv = callback.data.replace("aslot_et_","")
    if tv == "manual":
        await callback.message.edit_text("⏰ ЧЧ:ММ:"); await state.set_state(AddSlotStates.waiting_end_time_manual); return
    data = await state.get_data()
    sdt = parse_datetime(data['aslot_start_date'], data['aslot_start_time'])
    edt = parse_datetime(data['aslot_end_date'], tv)
    if not edt or edt <= sdt: return
    sid = data['addslot_spot_id']
    if db.check_slot_overlap(sid, sdt, edt):
        await callback.message.edit_text("❌ Пересечение с существующим слотом!")
        await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))
        await state.clear(); return
    db.create_spot_availability(sid, sdt, edt)
    await state.clear()
    await callback.message.edit_text(f"✅ Слот добавлен!\n📅 {format_datetime(sdt)} — {format_datetime(edt)}")
    await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))

@router.message(AddSlotStates.waiting_end_time_manual)
async def aslot_et_m(message: Message, state: FSMContext):
    if _cancel_check(message.text): await cancel_msg(message, state); return
    ok, r = validate_time(message.text)
    if not ok: await message.answer("❌"); return
    data = await state.get_data()
    sdt = parse_datetime(data['aslot_start_date'], data['aslot_start_time'])
    edt = parse_datetime(data['aslot_end_date'], r)
    if not edt or edt <= sdt: await message.answer("❌"); return
    sid = data['addslot_spot_id']
    if db.check_slot_overlap(sid, sdt, edt):
        await message.answer("❌ Пересечение с существующим слотом!")
        await state.clear(); return
    db.create_spot_availability(sid, sdt, edt)
    await state.clear()
    await message.answer(f"✅ Слот!\n📅 {format_datetime(sdt)} — {format_datetime(edt)}",
        reply_markup=get_main_menu_keyboard(_adm(message.from_user.id)))

# Удалить место
@router.callback_query(F.data.startswith("delspot_"))
async def delspot(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    sid = int(callback.data.replace("delspot_",""))
    db.delete_spot(sid)
    await callback.message.edit_text("✅ Место удалено.")
    await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))


# ==================== MY BOOKINGS ====================
@router.message(F.text == "📋 Мои бронирования")
async def my_bookings(message: Message, state: FSMContext):
    user = db.get_user_by_telegram_id(message.from_user.id)
    if not user: await message.answer("❌ /start"); return
    bookings = db.get_user_bookings(user['id'])
    if not bookings: await message.answer("😔 Нет бронирований."); return
    buttons = []
    for b in bookings[:15]:
        s = datetime.fromisoformat(b['start_time'])
        e = datetime.fromisoformat(b['end_time'])
        st = {
            "pending": "⏳",
            "paid_wait_admin": "💳",
            "confirmed": "✅",
            "cancelled": "❌",
            "expired": "⌛️",
            "completed": "✔️",
        }.get(b['status'], '')
        show_spot = b['status'] in ('confirmed', 'completed')
        spot_text = b['spot_number'] if show_spot else "🅿️ скрыто"
        text = f"{st} {spot_text} {s.strftime('%d.%m %H:%M')}-{e.strftime('%d.%m %H:%M')}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"mybk_{b['id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")])
    await message.answer("📋 <b>Бронирования:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("mybk_"))
async def booking_detail(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("mybk_",""))
    b = db.get_booking_by_id(bid)
    if not b: await callback.message.edit_text("❌ Не найдена."); return
    s = datetime.fromisoformat(b['start_time'])
    e = datetime.fromisoformat(b['end_time'])
    st = {
        "pending": "⏳ Ожидает оплаты",
        "paid_wait_admin": "💳 Чек отправлен",
        "confirmed": "✅ Подтверждена",
        "cancelled": "❌ Отменена",
        "expired": "⌛️ Истекла",
        "completed": "✔️ Завершена",
    }.get(b['status'], '')
    show_spot = b['status'] in ('confirmed', 'completed')
    spot_line = f"🏠 {b['spot_number']}\n" if show_spot else "🅿️ Номер места скрыт до подтверждения оплаты.\n"
    addr = b.get('address') or "—"
    await callback.message.edit_text(
        f"📋 <b>Бронь #{bid}</b>\n\n"
        f"{spot_line}"
        f"📍 {addr}\n"
        f"📅 {format_datetime(s)} — {format_datetime(e)}\n"
        f"📊 {st}",
        reply_markup=get_booking_detail_keyboard(b, b['customer_id']),
        parse_mode="HTML",
    )

@router.callback_query(F.data == "back_bookings")
async def back_bk(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user = db.get_user_by_telegram_id(callback.from_user.id)
    bookings = db.get_user_bookings(user['id'])
    buttons = []
    for b in bookings[:15]:
        s = datetime.fromisoformat(b['start_time'])
        e = datetime.fromisoformat(b['end_time'])
        st = {
            "pending": "⏳",
            "paid_wait_admin": "💳",
            "confirmed": "✅",
            "cancelled": "❌",
            "expired": "⌛️",
            "completed": "✔️",
        }.get(b['status'], '')
        show_spot = b['status'] in ('confirmed', 'completed')
        spot_text = b['spot_number'] if show_spot else "🅿️ скрыто"
        text = f"{st} {spot_text} {s.strftime('%d.%m %H:%M')}-{e.strftime('%d.%m %H:%M')}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"mybk_{b['id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")])
    await callback.message.edit_text("📋 <b>Бронирования:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("cancel_booking_"))
async def cancel_bk(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("cancel_booking_",""))
    db.cancel_booking(bid)
    await callback.message.edit_text(f"❌ Бронь #{bid} отменена.")
    await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))


# ==================== REVIEWS ====================
@router.callback_query(F.data.startswith("review_start_"))
async def review_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("review_start_",""))
    booking = db.get_booking_by_id(bid)
    if not booking or booking.get('reviewed'):
        await callback.message.answer("❌ Отзыв уже оставлен."); return
    await state.update_data(review_booking_id=bid, review_spot_id=booking['spot_id'],
                            review_supplier_id=booking['supplier_id'])
    await callback.message.edit_text(f"⭐ <b>Оцените {booking['spot_number']}</b>:",
        reply_markup=get_rating_keyboard(bid), parse_mode="HTML")
    await state.set_state(ReviewStates.waiting_rating)

@router.callback_query(ReviewStates.waiting_rating, F.data.startswith("rate_"))
async def review_rate(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    rating = int(callback.data.split("_")[2])
    await state.update_data(review_rating=rating)
    await callback.message.edit_text(f"⭐ {'⭐'*rating}\n\n💬 Комментарий (или пропустить):",
        reply_markup=get_review_skip_comment_keyboard(), parse_mode="HTML")
    await state.set_state(ReviewStates.waiting_comment)

@router.callback_query(ReviewStates.waiting_comment, F.data == "review_nocomment")
async def review_nocomment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    user = db.get_user_by_telegram_id(callback.from_user.id)
    db.create_review(data['review_booking_id'], user['id'], data['review_spot_id'],
                     data['review_supplier_id'], data['review_rating'])
    await state.clear()
    await callback.message.edit_text("✅ Отзыв!")
    await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))

@router.message(ReviewStates.waiting_comment)
async def review_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    user = db.get_user_by_telegram_id(message.from_user.id)
    db.create_review(data['review_booking_id'], user['id'], data['review_spot_id'],
                     data['review_supplier_id'], data['review_rating'], message.text[:500])
    await state.clear()
    await message.answer("✅ Отзыв!", reply_markup=get_main_menu_keyboard(_adm(message.from_user.id)))


# ==================== PROFILE ====================
@router.message(F.text == "👤 Профиль")
async def profile(message: Message, state: FSMContext):
    user = db.get_user_by_telegram_id(message.from_user.id)
    if not user: await message.answer("❌ /start"); return
    card = f"\n💳 {user['bank']}: {mask_card(user['card_number'])}" if user.get('card_number') else ""
    car = ""
    if user.get('license_plate'):
        car = f"\n🚗 {user['car_brand']} {user['car_color']} ({user['license_plate']})"
    await message.answer(
        f"👤 <b>Профиль</b>\n\n📛 {user['full_name']}\n📞 {user['phone']}{card}{car}",
        reply_markup=get_profile_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "edit_name")
async def edit_name(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("📝 Новое имя:")
    await state.set_state(EditProfileStates.waiting_name)

@router.message(EditProfileStates.waiting_name)
async def save_name(message: Message, state: FSMContext):
    ok, r = validate_name(message.text)
    if not ok: await message.answer(r); return
    user = db.get_user_by_telegram_id(message.from_user.id)
    db.update_user(user['id'], full_name=r); await state.clear()
    await message.answer(f"✅ Имя: {r}", reply_markup=get_main_menu_keyboard(_adm(message.from_user.id)))

@router.callback_query(F.data == "edit_phone")
async def edit_phone(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("📞 Новый номер:")
    await state.set_state(EditProfileStates.waiting_phone)

@router.message(EditProfileStates.waiting_phone)
async def save_phone(message: Message, state: FSMContext):
    if message.contact:
        phone = message.contact.phone_number
        if phone.startswith('+'): phone = phone[1:]
        if phone.startswith('7') and len(phone) == 11: phone = '8' + phone[1:]
        r = phone
    else:
        ok, r = validate_phone(message.text)
        if not ok: await message.answer(r); return
    user = db.get_user_by_telegram_id(message.from_user.id)
    db.update_user(user['id'], phone=r); await state.clear()
    await message.answer(f"✅ Телефон: {r}", reply_markup=get_main_menu_keyboard(_adm(message.from_user.id)))

@router.callback_query(F.data == "edit_car")
async def edit_car(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("🚗 Гос. номер:")
    await state.set_state(CarInfoStates.waiting_license_plate)

@router.callback_query(F.data == "edit_card")
async def edit_card(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("💳 16 цифр:")
    await state.set_state(CardInfoStates.waiting_card)

@router.callback_query(EditProfileStates.waiting_bank, F.data.startswith("bank_"))
async def edit_bank(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bank = callback.data.replace("bank_","")
    if bank == "Другой":
        await callback.message.edit_text("🏦 Введите название банка:")
        await state.set_state(EditProfileStates.waiting_bank_name); return
    data = await state.get_data()
    user = db.get_user_by_telegram_id(callback.from_user.id)
    db.update_user(user['id'], card_number=data['card_number'], bank=bank)
    await state.clear()
    await callback.message.edit_text(f"✅ Карта: {bank}")
    await callback.message.answer("Меню:", reply_markup=get_main_menu_keyboard(_adm(callback.from_user.id)))

@router.message(EditProfileStates.waiting_bank_name)
async def edit_bank_manual(message: Message, state: FSMContext):
    bank = message.text.strip()
    if len(bank) < 2 or len(bank) > 30: await message.answer("❌ 2-30 символов"); return
    data = await state.get_data()
    user = db.get_user_by_telegram_id(message.from_user.id)
    db.update_user(user['id'], card_number=data['card_number'], bank=bank)
    await state.clear()
    await message.answer(f"✅ Карта: {bank}", reply_markup=get_main_menu_keyboard(_adm(message.from_user.id)))


# ==================== NOTIFICATIONS ====================
@router.callback_query(F.data == "notify_available")
async def notify_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("🔔 <b>Уведомление</b>:", reply_markup=get_notify_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "notify_any")
async def notify_any(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user = db.get_user_by_telegram_id(callback.from_user.id)
    db.create_spot_notification(user['id'])
    await callback.message.edit_text("✅ Уведомим!")

@router.callback_query(F.data == "notify_date")
async def notify_date(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("📅 На какую дату?", reply_markup=get_dates_keyboard("ndate"))
    await state.set_state(NotifyStates.waiting_date)

@router.callback_query(NotifyStates.waiting_date, F.data.startswith("ndate_"))
async def ndate(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dv = callback.data.replace("ndate_","")
    if dv in ("manual","all"): return
    user = db.get_user_by_telegram_id(callback.from_user.id)
    ok, _ = validate_date(dv)
    if not ok: return
    date_obj = datetime.strptime(dv, "%d.%m.%Y")
    db.create_spot_notification(user['id'], desired_date=date_obj.strftime("%Y-%m-%d"), notify_any=False)
    await state.clear()
    await callback.message.edit_text(f"✅ Уведомим на {dv}!")


from aiogram.fsm.state import StatesGroup, State

class EditBooking(StatesGroup):
    booking_id = State()
    start_time = State()
    end_time = State()


@router.message(F.text == "⏱ Ближайшие слоты")
async def nearest_slots(message: Message, state: FSMContext):
    if await _check_ban(message): 
        return
    slots = db.get_nearest_free_slots(limit=12, days=AVAILABILITY_LOOKAHEAD_DAYS)
    if not slots:
        await message.answer("Сейчас нет доступных слотов.")
        return
    lines = ["⏱ <b>Ближайшие слоты</b> (номер места скрыт до подтверждения оплаты):\n"]
    for s in slots:
        st = datetime.fromisoformat(str(s["start_time"]))
        en = datetime.fromisoformat(str(s["end_time"]))
        addr = FIXED_ADDRESS
        if len(addr) > 30:
            addr = addr[:29] + "…"
        lines.append(
            f"📍 {addr} | 📅 {format_datetime(st)} — {format_datetime(en)}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data.startswith("booking_cancel_"))
async def booking_cancel_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("booking_cancel_", ""))

    b = db.get_booking_by_id(bid)
    ok = db.cancel_booking(bid)
    if ok:
        await callback.message.edit_text(f"❌ Бронь #{bid} отменена.")

        # Уведомление арендодателю, что бронь отменена и слот снова свободен
        if b and b.get('supplier_telegram_id'):
            try:
                await callback.bot.send_message(
                    b['supplier_telegram_id'],
                    f"❌ <b>Бронь #{bid} отменена</b>\n"
                    f"🏠 Место: {b['spot_number']}\n"
                    f"📅 {format_datetime(b['start_time'])} — {format_datetime(b['end_time'])}\n"
                    f"ℹ️ Интервал снова доступен для бронирования.",
                    parse_mode="HTML"
                )
            except:
                pass
    else:
        await callback.message.edit_text("❌ Не удалось отменить (возможно уже обработано).")

@router.callback_query(F.data.startswith("booking_paid_"))
async def booking_paid_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("booking_paid_", ""))
    st = db.get_booking_status(bid)
    if not st:
        await callback.message.answer("❌ Бронь не найдена.")
        return
    if st["status"] == "confirmed":
        await callback.message.answer("ℹ️ Эта бронь уже подтверждена администратором.")
        return
    if st["status"] == "paid_wait_admin":
        await callback.message.answer("ℹ️ Чек уже отправлен, ожидайте подтверждения.")
        return
    if st["status"] != "pending":
        await callback.message.answer("❌ Нельзя отметить оплату для этой брони.")
        return
    await state.update_data(paid_booking_id=bid)
    await state.set_state(PayReceiptStates.waiting_receipt)
    await callback.message.answer(
        f"📷 Отправьте сюда фото/скрин чека по брони #{bid}.\n"
        f"Также вы можете продублировать чек администратору {ADMIN_CHECK_USERNAME}."
    )

@router.message(PayReceiptStates.waiting_receipt)
async def receipt_upload(message: Message, state: FSMContext):
    if _cancel_check(message.text):
        await cancel_msg(message, state)
        return
    data = await state.get_data()
    bid = data.get("paid_booking_id")
    if not bid:
        await state.clear()
        await message.answer("❌ Данные потеряны.")
        return
    # принимаем фото или документ
    file_id = None
    kind = None
    if message.photo:
        file_id = message.photo[-1].file_id
        kind = "photo"
    elif message.document:
        file_id = message.document.file_id
        kind = "document"
    else:
        await message.answer("❌ Пришлите фото или файл чека (документ).")
        return

    ok = db.mark_booking_paid(bid)
    b = db.get_booking_full(bid)

    # Отправляем админам
    caption = f"🧾 <b>Чек по брони #{bid}</b>\n"
    if b:
        caption += f"🏠 {b.get('spot_number','')}\n"
        caption += f"📅 {b.get('start_time')} — {b.get('end_time')}\n"
        caption += f"💰 {b.get('total_price')}₽\n"
        if b.get('customer_username'):
            caption += f"👤 @{b['customer_username']}\n"
        else:
            caption += f"👤 {b.get('customer_name','')}\n"
        caption += f"📍 {FIXED_ADDRESS}"

        # Важно: админу нужно видеть, кому переводить деньги (поставщик места)
        sup_name = b.get('supplier_name') or ''
        sup_phone = b.get('supplier_phone') or ''
        sup_card = b.get('supplier_card') or ''
        sup_bank = b.get('supplier_bank') or ''
        if sup_name or sup_phone or sup_card:
            if sup_name:
                caption += f"\n👤 {sup_name}"
            if sup_phone:
                caption += f"\n📞 {sup_phone}"
            if sup_card:
                caption += f"\n💳 {sup_bank + ': ' if sup_bank else ''}<code>{sup_card}</code>"
    kb = admin_payment_review_keyboard(bid)
    for adm in db.get_admins():
        try:
            if kind == "photo":
                await message.bot.send_photo(adm["telegram_id"], file_id, caption=caption, reply_markup=kb, parse_mode="HTML")
            else:
                await message.bot.send_document(adm["telegram_id"], file_id, caption=caption, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass

    await state.clear()
    if ok:
        await message.answer("✅ Чек отправлен администратору. Ожидайте подтверждения.")
    else:
        await message.answer("ℹ️ Чек отправлен, но статус брони уже изменился. Ожидайте ответа.")


# ==================== CALLBACK FALLBACKS ====================
# If bot restarts and FSM state is lost, these handlers prevent endless "loading".

@router.callback_query(F.data.in_({"spot_confirm_yes","spot_confirm_no"}))
async def fallback_spot_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer("⚠️ Сессия истекла. Начните заново.", show_alert=True)
    try:
        await callback.message.answer("Нажмите /start и повторите действие.")
    except Exception:
        pass

@router.callback_query(F.data.startswith("spot_confirm_yes:"))
async def iron_spot_confirm_yes(callback: CallbackQuery):
    await callback.answer()  # stop Telegram spinner immediately
    cid = callback.data.split(":", 1)[1]
    try:
        from database import create_spot, add_availability, create_spot_confirm, get_slot_confirm, delete_slot_confirm  # noqa
    except Exception:
        from database import get_slot_confirm, delete_slot_confirm  # type: ignore

    data = get_slot_confirm(cid)
    if not data:
        await callback.message.answer("⚠️ Кнопка устарела. Нажмите /start и попробуйте снова.")
        return

    # Only the same user can confirm
    if callback.from_user.id != data["user_id"]:
        await callback.message.answer("⚠️ Это подтверждение не для вас.")
        return

    # Immediate feedback
    try:
        await callback.message.edit_text("⏳ Создаю слот...")
    except Exception:
        pass

    # Create spot + availability
    try:
        # create_spot may return spot_id; if spot already exists for user, fallback logic should be inside create_spot in your code.
        spot_id = create_spot(data["user_id"], data["spot_number"])
        add_availability(spot_id, data["start_time"], data["end_time"], data["price"])
        delete_slot_confirm(cid)
        await callback.message.answer("✅ Слот добавлен!")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при создании слота: {e}")

@router.callback_query(F.data.startswith("spot_confirm_no:"))
async def iron_spot_confirm_no(callback: CallbackQuery):
    await callback.answer()
    cid = callback.data.split(":", 1)[1]
    from database import delete_slot_confirm, get_slot_confirm
    data = get_slot_confirm(cid)
    if data and callback.from_user.id == data["user_id"]:
        delete_slot_confirm(cid)
    await callback.message.answer("Ок, отменил. Начните заново: /start")

## NOTE:
# Общий callback-fallback вынесен в отдельный router (fallback_handlers.py),
# чтобы он НЕ перехватывал callback'и админ-панели (и вообще любые другие роутеры).
