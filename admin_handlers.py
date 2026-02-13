"""
Админ-панель ParkingBot
"""
import logging, asyncio
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import database as db
import os
import sqlite3
import tempfile
from openpyxl import Workbook
from config import ADMIN_PASSWORD, FIXED_ADDRESS, DATABASE_PATH
from keyboards import *
from utils import *

logger = logging.getLogger(__name__)
router = Router()


def _admin_dates_keyboard(prefix: str, cancel_cb: str, days: int = 30) -> InlineKeyboardMarkup:
    dates = get_next_days(days)
    buttons = []
    for i in range(0, len(dates), 3):
        buttons.append([
            InlineKeyboardButton(text=dates[j][:5], callback_data=f"{prefix}_{dates[j]}")
            for j in range(i, min(i + 3, len(dates)))
        ])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _admin_times_keyboard(prefix: str, date_str: str, cancel_cb: str, min_dt: datetime | None = None) -> InlineKeyboardMarkup:
    # Только почасовые варианты (минуты 00)
    date_obj = datetime.strptime(date_str, "%d.%m.%Y")
    times = []
    for h in range(0, 24):
        dt = date_obj.replace(hour=h, minute=0, second=0, microsecond=0)
        if min_dt and dt < min_dt:
            continue
        times.append(dt.strftime("%H:%M"))

    buttons = []
    for i in range(0, len(times), 6):
        buttons.append([
            InlineKeyboardButton(text=times[j], callback_data=f"{prefix}_{times[j]}")
            for j in range(i, min(i + 6, len(times)))
        ])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

class AdminStates(StatesGroup):
    waiting_password = State()
    waiting_ban_reason = State()
    waiting_broadcast_message = State()
    waiting_edit_hours = State()
    waiting_user_search = State()


# ==================== USERS LIST (PAGINATION/SEARCH) ====================
USERS_PAGE_SIZE = 10

def _user_btn_text(u: dict) -> str:
    icon = "👑" if u.get('role') == 'admin' else "👤"
    if not u.get('is_active', True):
        icon = "🚫"
    name = (u.get('full_name') or "—").strip()
    uname = (u.get('username') or "").strip()
    if uname:
        uname_txt = f"@{uname}"
    else:
        # Чтобы "юзернейм" был всегда хоть какой-то идентификацией
        uname_txt = f"id:{u.get('telegram_id','')}"
    txt = f"{icon} {name} · {uname_txt}"
    # Telegram ограничивает длину текста кнопки
    if len(txt) > 64:
        txt = txt[:61] + "…"
    return txt

def _users_keyboard(users: list[dict], page: int, pages: int, nav_prefix: str, show_search: bool = True) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for u in users:
        buttons.append([InlineKeyboardButton(text=_user_btn_text(u), callback_data=f"adm_user_{u['id']}")])

    # Навигация
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{nav_prefix}_{page-1}"))
    else:
        nav_row.append(InlineKeyboardButton(text=" ", callback_data="noop"))
    nav_row.append(InlineKeyboardButton(text=f"{page+1}/{max(pages,1)}", callback_data="noop"))
    if page + 1 < pages:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"{nav_prefix}_{page+1}"))
    else:
        nav_row.append(InlineKeyboardButton(text=" ", callback_data="noop"))
    buttons.append(nav_row)

    if show_search:
        buttons.append([InlineKeyboardButton(text="🔎 Поиск", callback_data="admin_users_search")])
    else:
        buttons.append([InlineKeyboardButton(text="❌ Сброс поиска", callback_data="admin_users")])

    buttons.append([InlineKeyboardButton(text="🔙 Панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


class AdminSlotEditStates(StatesGroup):
    waiting_date = State()
    waiting_time = State()


# ==================== AUTH ====================
@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    """Команда /admin"""
    await state.clear()
    user = db.get_user_by_telegram_id(message.from_user.id)
    if not user:
        await message.answer("❌ Сначала /start"); return
    if user['role'] == 'admin':
        await message.answer("🔑 <b>Админ-панель</b>", reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")
    else:
        await message.answer("🔑 Введите пароль:")
        await state.set_state(AdminStates.waiting_password)

@router.message(F.text == "🔑 Админ-панель")
async def admin_start(message: Message, state: FSMContext):
    await state.clear()
    user = db.get_user_by_telegram_id(message.from_user.id)
    if not user: return
    if user['role'] == 'admin':
        await message.answer("🔑 <b>Админ-панель</b>", reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")
    else:
        await message.answer("🔑 Введите пароль:")
        await state.set_state(AdminStates.waiting_password)

@router.message(AdminStates.waiting_password)
async def admin_password(message: Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        user = db.get_user_by_telegram_id(message.from_user.id)
        db.set_user_role(user['id'], 'admin')
        db.create_admin_session(user['id'], message.from_user.id)
        await state.clear()
        await message.answer("✅ Вы админ!", reply_markup=get_main_menu_keyboard(True))
        await message.answer("🔑 <b>Админ-панель</b>", reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")
    else:
        await state.clear()
        await message.answer("❌ Неверный пароль.", reply_markup=get_main_menu_keyboard())


# ==================== BOOKING MANAGEMENT ====================
@router.callback_query(F.data == "admin_pending")
async def admin_pending(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bookings = db.get_pending_bookings()
    if not bookings:
        await callback.message.edit_text("✅ Нет ожидающих заявок.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Панель", callback_data="admin_panel")]]))
        return
    buttons = []
    for b in bookings[:20]:
        s = datetime.fromisoformat(b['start_time'])
        text = f"⏳ #{b['id']} {b['spot_number']} {s.strftime('%d.%m %H:%M')} — {b['customer_name']}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"adm_bk_{b['id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Панель", callback_data="admin_panel")])
    await callback.message.edit_text("📋 <b>Заявки на подтверждение:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data == "admin_all_bookings")
async def admin_all_bookings(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bookings = db.get_all_bookings(limit=20)
    if not bookings:
        await callback.message.edit_text("📋 Нет бронирований.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Панель", callback_data="admin_panel")]]))
        return
    buttons = []
    for b in bookings[:20]:
        s = datetime.fromisoformat(b['start_time'])
        st = {"pending":"⏳","confirmed":"✅","cancelled":"❌","completed":"✔️"}.get(b['status'],'')
        text = f"{st} #{b['id']} {b['spot_number']} {s.strftime('%d.%m')} {b['customer_name']}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"adm_bk_{b['id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Панель", callback_data="admin_panel")])
    await callback.message.edit_text("📊 <b>Все бронирования:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("adm_bk_"))
async def admin_booking_detail(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("adm_bk_",""))
    b = db.get_booking_by_id(bid)
    if not b: await callback.message.edit_text("❌ Не найдена."); return
    s = datetime.fromisoformat(b['start_time'])
    e = datetime.fromisoformat(b['end_time'])
    h = (e-s).total_seconds()/3600
    st = {"pending":"⏳ Ожидает","confirmed":"✅ Подтверждена","cancelled":"❌ Отменена","completed":"✔️ Завершена"}.get(b['status'],'')
    car = ""
    if b.get('customer_plate'): car = f"\n🚗 {b['customer_car']} {b['customer_car_color']} ({b['customer_plate']})"
    text = (
        f"📋 <b>Бронь #{bid}</b>\n\n"
        f"📊 {st}\n"
        f"🏠 {b['spot_number']}\n"
        f"📅 {format_datetime(s)} — {format_datetime(e)}\n"
        f"⏱ {h:.1f}ч\n\n"
        f"🔵 <b>Арендатор:</b>\n👤 {b['customer_name']}\n📞 {b['customer_phone']}")
    if b.get('customer_username'): text += f"\n📱 @{b['customer_username']}"
    text += car
    text += f"\n\n🟢 <b>Поставщик:</b>\n👤 {b['supplier_name']}\n📞 {b.get('supplier_phone','')}"
    if b.get('supplier_username'): text += f"\n📱 @{b['supplier_username']}"
    if b.get('card_number'): text += f"\n💳 {b.get('bank','')}: {b['card_number']}"
    await callback.message.edit_text(text,
        reply_markup=get_admin_booking_keyboard(bid, b['status']), parse_mode="HTML")

@router.callback_query(F.data.startswith("adm_confirm_"))
async def admin_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("adm_confirm_",""))
    ok, status = db.confirm_booking_idempotent(bid)

    if status == 'already':
        try:
            await callback.message.edit_text(f"ℹ️ Бронь #{bid} уже подтверждена.")
        except:
            await callback.message.answer(f"ℹ️ Бронь #{bid} уже подтверждена.")
        return

    if status == 'not_paid':
        await callback.message.answer(f"⏳ Бронь #{bid} ещё не отмечена как оплаченная (ждём чек).")
        return

    if not ok:
        await callback.message.answer(f"❌ Не удалось подтвердить бронь #{bid}.")
        return

    b = db.get_booking_by_id(bid)
    await callback.message.edit_text(f"✅ Бронь #{bid} подтверждена!")

    # Финальное сообщение пользователю с адресом
    try:
        await callback.bot.send_message(
            b['customer_telegram_id'],
            f"🎉 <b>Всё подтверждено!</b>\n\n"
            f"🏠 {b['spot_number']}\n"
            f"📍 {FIXED_ADDRESS}\n"
            f"📅 {format_datetime(b['start_time'])} — {format_datetime(b['end_time'])}",
            parse_mode="HTML"
        )
    except:
        pass

    # Сообщение арендодателю (поставщику), что слот взяли и оплатили (без контактов арендатора)
    try:
        if b.get('supplier_telegram_id'):
            await callback.bot.send_message(
                b['supplier_telegram_id'],
                f"✅ Ваш слот забронирован и оплачен!\n\n📅 {format_datetime(b['start_time'])} — {format_datetime(b['end_time'])}",
                parse_mode="HTML"
            )
    except:
        pass

    db.log_admin_action('booking_confirmed', booking_id=bid)

@router.callback_query(F.data.startswith("adm_reject_"))
async def admin_reject(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("adm_reject_",""))
    b = db.get_booking_by_id(bid)
    db.reject_booking(bid)
    await callback.message.edit_text(f"❌ Бронь #{bid} отклонена.")
    if b:
        try:
            await callback.bot.send_message(
                b['customer_telegram_id'],
                f"❌ <b>Бронь #{bid} отклонена.</b>\n🅿️ Номер места не раскрывается.",
                parse_mode="HTML"
            )
        except: pass
    db.log_admin_action('booking_rejected', booking_id=bid)

@router.callback_query(F.data.startswith("adm_cancel_"))
async def admin_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("adm_cancel_",""))
    b = db.get_booking_by_id(bid)
    db.cancel_booking(bid)
    await callback.message.edit_text(f"❌ Бронь #{bid} отменена админом.")
    if b:
        try:
            await callback.bot.send_message(
                b['customer_telegram_id'],
                f"❌ <b>Бронь #{bid} отменена администратором.</b>",
                parse_mode="HTML"
            )
        except:
            pass

        # уведомление арендодателя
        try:
            if b.get('supplier_telegram_id'):
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

    db.log_admin_action('booking_cancelled_admin', booking_id=bid)

@router.callback_query(F.data.startswith("adm_edit_"))
async def admin_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("adm_edit_",""))
    b = db.get_booking_by_id(bid)
    if not b:
        return

    s = datetime.fromisoformat(b['start_time'])
    e = datetime.fromisoformat(b['end_time'])
    full_hours = int(((e - s).total_seconds() + 3600 - 1) // 3600)
    if full_hours < 1:
        full_hours = 1

    # Клавиатура выбора оплаченных часов (только кнопками)
    buttons = []
    row = []
    for h in range(1, min(full_hours, 48) + 1):
        row.append(InlineKeyboardButton(text=str(h), callback_data=f"adm_sethours_{bid}_{h}"))
        if len(row) == 6:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])

    await callback.message.edit_text(
        f"✏️ <b>Редактирование брони #{bid}</b>\n\n"
        f"📅 {format_datetime(s)} — {format_datetime(e)}\n"
        f"⏱ Полный интервал: <b>{full_hours}ч</b>\n\n"
        f"Выберите, сколько часов <b>оплачено</b> (остаток вернётся свободным слотом):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await state.clear()

@router.callback_query(F.data.startswith("adm_sethours_"))
async def admin_set_hours(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        parts = callback.data.split("_")  # adm_sethours_{bid}_{h}
        bid = int(parts[2])
        hours = int(parts[3])
    except Exception:
        await callback.message.answer("❌ Ошибка данных.")
        return

    ok = db.admin_edit_booking_hours(bid, hours)
    if not ok:
        await callback.message.edit_text("❌ Не удалось обновить бронь.")
        return

    b = db.get_booking_by_id(bid)
    await callback.message.edit_text(f"✅ Бронь #{bid} обновлена: оплачено {hours}ч. Остаток снова свободен.")
    db.log_admin_action('booking_edited', booking_id=bid, details=f"paid={hours}h")

    # уведомим арендатора
    if b:
        try:
            await callback.bot.send_message(
                b['customer_telegram_id'],
                f"📝 <b>Бронь #{bid} обновлена администратором.</b>\n"
                f"✅ Оплачено: {hours}ч\n"
                f"📅 {format_datetime(b['start_time'])} — {format_datetime(b['end_time'])}",
                parse_mode="HTML"
            )
        except:
            pass

@router.message(AdminStates.waiting_edit_hours)
async def admin_edit_hours(message: Message, state: FSMContext):
    # Редактирование теперь делается только кнопками.
    await state.clear()
    await message.answer("ℹ️ Редактирование брони делается кнопками. Откройте бронь и нажмите «✏️ Редактировать».",
                         reply_markup=get_main_menu_keyboard(True))


# ==================== SLOT MANAGEMENT ====================
@router.callback_query(F.data == "admin_slots")
async def admin_slots(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    spots = db.get_spots_with_free_availabilities()
    if not spots:
        await callback.message.edit_text("🏠 Нет активных мест со свободными слотами.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Панель", callback_data="admin_panel")]]))
        return
    buttons = []
    for sp in spots[:20]:
        buttons.append([InlineKeyboardButton(text=f"🏠 {sp['spot_number']} ({sp['supplier_name']})",
            callback_data=f"adm_spot_{sp['id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Панель", callback_data="admin_panel")])
    await callback.message.edit_text("🏠 <b>Места:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("adm_spot_"))
async def admin_spot_detail(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    sid = int(callback.data.replace("adm_spot_",""))
    # Для админки показываем и свободные, и забронированные интервалы
    avails = db.get_spot_availabilities_all(sid)
    spot = db.get_spot_by_id(sid)
    if not spot: return
    buttons = []
    for a in avails[:20]:
        s = datetime.fromisoformat(str(a['start_time']))
        e = datetime.fromisoformat(str(a['end_time']))
        icon = "🔴" if a.get('is_booked') else "🟢"
        bid = a.get('booking_id')
        suffix = f" #{bid}" if bid else ""
        text = f"{icon} {s.strftime('%d.%m %H:%M')}-{e.strftime('%d.%m %H:%M')}{suffix}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"adm_sa_{a['id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_slots")])
    await callback.message.edit_text(f"🏠 <b>{spot['spot_number']}</b> — слоты:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("adm_sa_"))
async def admin_slot_action(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    aid = int(callback.data.replace("adm_sa_",""))
    slot = db.get_availability_by_id(aid)
    if not slot: return
    s = datetime.fromisoformat(str(slot['start_time']))
    e = datetime.fromisoformat(str(slot['end_time']))
    status = "🔴 Забронирован" if slot.get('is_booked') else "🟢 Свободен"
    bid = slot.get('booking_id')
    note = f"\n🧾 Привязан к брони #{bid}" if bid else ""
    can_edit = bid is None
    await callback.message.edit_text(
        f"📅 {format_datetime(s)} — {format_datetime(e)}\n{status}{note}",
        reply_markup=get_admin_slot_actions_keyboard(aid, slot['spot_id'], bool(slot.get('is_booked')), can_edit),
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("adm_toggle_"))
async def admin_toggle(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    aid = int(callback.data.replace("adm_toggle_",""))
    new_status = db.admin_toggle_slot(aid)
    if new_status == -1:
        await callback.message.edit_text("❌ Этот слот привязан к брони — менять статус нельзя.")
        return
    if new_status is None:
        await callback.message.edit_text("❌ Слот не найден.")
        return
    st = "🔴 забронированным" if new_status else "🟢 свободным"
    await callback.message.edit_text(f"✅ Слот стал {st}.")
    db.log_admin_action('slot_toggled', details=f"slot={aid}, booked={new_status}")


@router.callback_query(F.data.startswith("adm_delslot_"))
async def admin_delete_slot(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    slot_id = int(callback.data.replace("adm_delslot_", ""))
    slot = db.get_slot_by_id(slot_id)
    if not slot:
        await callback.message.edit_text("❌ Слот не найден.")
        return
    spot_id = slot['spot_id']
    ok = db.admin_delete_availability(slot_id)
    if not ok:
        await callback.message.edit_text("❌ Нельзя удалить: слот привязан к брони или данные некорректны.")
        return
    db.log_admin_action('slot_deleted', spot_id=spot_id, details=f"slot={slot_id}")
    await callback.message.edit_text(
        "✅ Слот удалён.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"adm_spot_{spot_id}")]
        ]),
    )


@router.callback_query(F.data.startswith("adm_editstart_"))
async def admin_edit_slot_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    slot_id = int(callback.data.replace("adm_editstart_", ""))
    slot = db.get_slot_by_id(slot_id)
    if not slot:
        await callback.message.edit_text("❌ Слот не найден.")
        return
    if slot.get('booking_id') is not None:
        await callback.message.edit_text("❌ Слот привязан к брони — редактировать нельзя.")
        return
    await state.clear()
    await state.update_data(slot_id=slot_id, spot_id=slot['spot_id'], field='start')
    await callback.message.edit_text(
        "⏱ <b>Изменить начало</b>\n\nВыберите дату:",
        reply_markup=_admin_dates_keyboard(prefix="adm_sedate", cancel_cb=f"adm_sa_{slot_id}"),
        parse_mode="HTML",
    )
    await state.set_state(AdminSlotEditStates.waiting_date)


@router.callback_query(F.data.startswith("adm_editend_"))
async def admin_edit_slot_end(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    slot_id = int(callback.data.replace("adm_editend_", ""))
    slot = db.get_slot_by_id(slot_id)
    if not slot:
        await callback.message.edit_text("❌ Слот не найден.")
        return
    if slot.get('booking_id') is not None:
        await callback.message.edit_text("❌ Слот привязан к брони — редактировать нельзя.")
        return
    await state.clear()
    await state.update_data(slot_id=slot_id, spot_id=slot['spot_id'], field='end')
    await callback.message.edit_text(
        "⏱ <b>Изменить конец</b>\n\nВыберите дату:",
        reply_markup=_admin_dates_keyboard(prefix="adm_sedate", cancel_cb=f"adm_sa_{slot_id}"),
        parse_mode="HTML",
    )
    await state.set_state(AdminSlotEditStates.waiting_date)


@router.callback_query(AdminSlotEditStates.waiting_date, F.data.startswith("adm_sedate_"))
async def admin_slot_edit_date(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    date_str = callback.data.replace("adm_sedate_", "")
    # validate_date() использует datetime.now() сервера (может быть UTC),
    # поэтому валидируем относительно now_local().
    try:
        parsed = datetime.strptime(date_str, "%d.%m.%Y")
        ok = parsed.date() >= now_local().date()
    except Exception:
        ok = False
    data = await state.get_data()
    slot_id = data.get('slot_id')
    if not ok or not slot_id:
        await callback.message.edit_text("❌ Неверная дата.")
        await state.clear()
        return
    await state.update_data(date_str=date_str)

    # Если выбран сегодняшний день — не даём выбрать прошедшие часы
    min_dt = None
    try:
        if parsed.date() == now_local().date():
            min_dt = now_local()
    except Exception:
        min_dt = None

    await callback.message.edit_text(
        "⏱ Выберите время (только часы):",
        reply_markup=_admin_times_keyboard(prefix="adm_setime", date_str=date_str, cancel_cb=f"adm_sa_{slot_id}", min_dt=min_dt),
        parse_mode="HTML",
    )
    await state.set_state(AdminSlotEditStates.waiting_time)


@router.callback_query(AdminSlotEditStates.waiting_time, F.data.startswith("adm_setime_"))
async def admin_slot_edit_time(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    time_str = callback.data.replace("adm_setime_", "")
    ok_time, t_norm = validate_time(time_str)
    data = await state.get_data()
    slot_id = data.get('slot_id')
    field = data.get('field')
    date_str = data.get('date_str')
    if not (ok_time and slot_id and field and date_str):
        await callback.message.edit_text("❌ Ошибка выбора времени.")
        await state.clear()
        return

    new_dt = parse_datetime(date_str, t_norm)
    if not new_dt:
        await callback.message.edit_text("❌ Ошибка даты/времени.")
        await state.clear()
        return
    if new_dt < now_local():
        await callback.message.edit_text("❌ Нельзя выбрать время в прошлом.")
        return

    # Актуальные значения слота
    slot = db.get_slot_by_id(slot_id)
    if not slot:
        await callback.message.edit_text("❌ Слот не найден.")
        await state.clear()
        return
    if slot.get('booking_id') is not None:
        await callback.message.edit_text("❌ Слот привязан к брони — редактировать нельзя.")
        await state.clear()
        return

    cur_start = datetime.fromisoformat(str(slot['start_time']))
    cur_end = datetime.fromisoformat(str(slot['end_time']))
    if field == 'start':
        new_start, new_end = new_dt, cur_end
    else:
        new_start, new_end = cur_start, new_dt

    ok = db.admin_update_availability_interval(slot_id, new_start, new_end)
    if not ok:
        await callback.message.edit_text("❌ Не удалось обновить слот (проверьте, что нет пересечений и конец позже начала).")
        return
    db.log_admin_action('slot_edited', spot_id=slot['spot_id'], details=f"slot={slot_id}, field={field}")
    await state.clear()
    # Вернёмся к карточке слота
    await callback.message.edit_text("✅ Слот обновлён.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ К слоту", callback_data=f"adm_sa_{slot_id}")],
        [InlineKeyboardButton(text="🔙 К месту", callback_data=f"adm_spot_{slot['spot_id']}")],
    ]))


# ==================== USERS ====================
@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    # Сбросим поиск
    try:
        await state.update_data(user_search_query=None)
    except Exception:
        pass
    await _show_users_page(callback, state, page=0, mode="all")


async def _show_users_page(callback: CallbackQuery, state: FSMContext, page: int, mode: str = "all"):
    page = max(0, int(page))
    if mode == "search":
        data = await state.get_data()
        q = (data.get('user_search_query') or "").strip()
        if not q:
            await callback.message.edit_text(
                "🔎 Введите поисковый запрос через кнопку «Поиск».",
                reply_markup=_users_keyboard([], 0, 1, "admin_users_page", show_search=True),
            )
            return
        total = db.search_users_count(q)
        pages = max(1, (total + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
        page = min(page, pages - 1)
        users = db.search_users(q, limit=USERS_PAGE_SIZE, offset=page * USERS_PAGE_SIZE)
        text = f"👥 <b>Пользователи</b>\n🔎 <b>Поиск:</b> {q}\nВсего: {total}"
        kb = _users_keyboard(users, page, pages, "admin_users_search_page", show_search=False)
    else:
        total = db.get_users_count()
        pages = max(1, (total + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
        page = min(page, pages - 1)
        users = db.get_all_users(limit=USERS_PAGE_SIZE, offset=page * USERS_PAGE_SIZE)
        text = f"👥 <b>Пользователи ({total})</b>"
        kb = _users_keyboard(users, page, pages, "admin_users_page", show_search=True)

    if not users:
        if mode == "search":
            text += "\n\n😕 Ничего не найдено."
        else:
            text += "\n\n😕 Список пуст."

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_users_page_"))
async def admin_users_page(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    page = int(callback.data.replace("admin_users_page_", "") or 0)
    await _show_users_page(callback, state, page=page, mode="all")


@router.callback_query(F.data.startswith("admin_users_search_page_"))
async def admin_users_search_page(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    page = int(callback.data.replace("admin_users_search_page_", "") or 0)
    await _show_users_page(callback, state, page=page, mode="search")


@router.callback_query(F.data == "admin_users_search")
async def admin_users_search(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(
        user_search_origin_chat_id=callback.message.chat.id,
        user_search_origin_msg_id=callback.message.message_id,
    )
    await callback.message.edit_text(
        "🔎 <b>Поиск пользователя</b>\n\n"
        "Введите имя / телефон / @username / telegram_id:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")],
        ]),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.waiting_user_search)


@router.message(AdminStates.waiting_user_search)
async def admin_users_search_query(message: Message, state: FSMContext):
    q = (message.text or "").strip()
    if not q or q.lower() in ("отмена", "cancel"):
        data = await state.get_data()
        chat_id = data.get('user_search_origin_chat_id')
        msg_id = data.get('user_search_origin_msg_id')
        await state.set_state(None)

        total = db.get_users_count()
        pages = max(1, (total + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
        users = db.get_all_users(limit=USERS_PAGE_SIZE, offset=0)
        text = f"👥 <b>Пользователи ({total})</b>"
        kb = _users_keyboard(users, 0, pages, "admin_users_page", show_search=True)

        try:
            if chat_id and msg_id:
                await message.bot.edit_message_text(
                    text,
                    chat_id=chat_id,
                    message_id=msg_id,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
            else:
                await message.answer(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    data = await state.get_data()
    chat_id = data.get('user_search_origin_chat_id')
    msg_id = data.get('user_search_origin_msg_id')

    await state.update_data(user_search_query=q)
    await state.set_state(None)

    total = db.search_users_count(q)
    users = db.search_users(q, limit=USERS_PAGE_SIZE, offset=0)
    pages = max(1, (total + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
    text = f"👥 <b>Пользователи</b>\n🔎 <b>Поиск:</b> {q}\nВсего: {total}"
    kb = _users_keyboard(users, 0, pages, "admin_users_search_page", show_search=False)
    if not users:
        text += "\n\n😕 Ничего не найдено."

    try:
        if chat_id and msg_id:
            await message.bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=msg_id,
                reply_markup=kb,
                parse_mode="HTML",
            )
        else:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()

@router.callback_query(F.data.startswith("adm_user_"))
async def admin_user_detail(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    uid = int(callback.data.replace("adm_user_",""))
    user = db.get_user_by_id(uid)
    if not user: return
    card = f"\n💳 {user['bank']}: {user['card_number']}" if user.get('card_number') else ""
    car = ""
    if user.get('license_plate'):
        car = f"\n🚗 {user['car_brand']} {user['car_color']} ({user['license_plate']})"
    ban = ""
    if not user['is_active']:
        if user.get('banned_until'):
            ban = f"\n🚫 Бан до {format_datetime(user['banned_until'])}"
        else: ban = "\n🚫 Перманентный бан"
        if user.get('ban_reason'): ban += f" ({user['ban_reason']})"
    uname = (user.get('username') or "").strip()
    uname_line = f"@{uname}" if uname else "—"
    profile_link = f"<a href=\"tg://user?id={user['telegram_id']}\">открыть профиль</a>"
    text = (
        f"👤 <b>{user['full_name']}</b>\n"
        f"📞 {user['phone']}\n"
        f"📱 {uname_line} · {profile_link}{card}{car}{ban}"
    )
    await callback.message.edit_text(text,
        reply_markup=get_user_admin_actions_keyboard(uid, user), parse_mode="HTML")

@router.callback_query(F.data.startswith("set_admin_"))
async def set_admin(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    db.set_user_role(int(callback.data.replace("set_admin_","")), 'admin')
    await callback.message.edit_text("✅ Теперь админ.")

@router.callback_query(F.data.startswith("set_user_"))
async def set_user(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    db.set_user_role(int(callback.data.replace("set_user_","")), 'user')
    await callback.message.edit_text("✅ Теперь обычный пользователь.")

@router.callback_query(F.data.startswith("ban_menu_"))
async def ban_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    uid = int(callback.data.replace("ban_menu_",""))
    await callback.message.edit_text("⏱ Длительность бана:", reply_markup=get_ban_duration_keyboard(uid))

@router.callback_query(F.data.startswith("ban_"))
async def ban_duration(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    parts = callback.data.split("_")
    if len(parts) != 3: return
    uid = int(parts[1]); hours = int(parts[2])
    await state.update_data(ban_user_id=uid, ban_hours=hours if hours > 0 else None)
    await callback.message.edit_text("📝 Причина бана (или «-» без причины):")
    await state.set_state(AdminStates.waiting_ban_reason)

@router.message(AdminStates.waiting_ban_reason)
async def ban_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    reason = "" if message.text == "-" else message.text[:200]
    db.ban_user(data['ban_user_id'], data.get('ban_hours'), reason)
    await state.clear()
    user = db.get_user_by_id(data['ban_user_id'])
    await message.answer(f"🚫 {user['full_name']} забанен.", reply_markup=get_main_menu_keyboard(True))
    try:
        t = "🚫 Вы заблокированы"
        if data.get('ban_hours'): t += f" на {data['ban_hours']}ч"
        else: t += " навсегда"
        if reason: t += f"\n📝 {reason}"
        await message.bot.send_message(user['telegram_id'], t)
    except: pass

@router.callback_query(F.data.startswith("unban_"))
async def unban(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    db.unban_user(int(callback.data.replace("unban_","")))
    await callback.message.edit_text("✅ Разбанен.")


# ==================== STATS ====================
@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    s = db.get_statistics()
    await callback.message.edit_text(
        f"📈 <b>Статистика</b>\n\n"
        f"👥 Пользователи: {s['total_users']} (активных: {s['active_users']})\n"
        f"🏠 Мест: {s['total_spots']}\n"
        f"📋 Бронирований: {s['total_bookings']}\n"
        f"⏳ Ожидает: {s['pending_bookings']}\n"
        f"✅ Подтверждено: {s['confirmed_bookings']}\n"
        f"💰 Доход: {s['total_revenue']}₽",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Панель", callback_data="admin_panel")]]),
        parse_mode="HTML")

# ==================== BROADCAST ====================
@router.callback_query(F.data == "admin_broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("📢 Кому отправить?", reply_markup=get_broadcast_target_keyboard())

@router.callback_query(F.data.startswith("broadcast_"))
async def broadcast_target(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(broadcast_target=callback.data.replace("broadcast_",""))
    await callback.message.edit_text("📝 Введите текст рассылки:")
    await state.set_state(AdminStates.waiting_broadcast_message)

@router.message(AdminStates.waiting_broadcast_message)
async def broadcast_send(message: Message, state: FSMContext):
    data = await state.get_data()
    target = data.get('broadcast_target','all')
    users = db.get_active_users() if target == 'active' else db.get_all_users(limit=10000)
    await state.clear()
    sent = 0; fail = 0
    for u in users:
        try:
            await message.bot.send_message(u['telegram_id'], message.text)
            sent += 1
            if sent % 20 == 0: await asyncio.sleep(0.5)
        except: fail += 1
    await message.answer(f"📢 Отправлено: {sent}, ошибок: {fail}", reply_markup=get_main_menu_keyboard(True))


# ==================== NAV ====================
@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("🔑 <b>Админ-панель</b>",
        reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "admin_export_db")
async def admin_export_db(callback: CallbackQuery):
    await callback.answer()
    try:
        file = FSInputFile(DATABASE_PATH)
        await callback.message.answer_document(file, caption="💾 Резервная копия базы данных")
    except Exception as e:
        await callback.message.answer(f"Не удалось выгрузить базу: {e}")


@router.callback_query(F.data.startswith("adm_pay_confirm_"))
async def admin_pay_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("adm_pay_confirm_", ""))
    ok, status = db.confirm_booking_idempotent(bid)
    if status == 'already':
        await callback.message.answer(f"ℹ️ Бронь #{bid} уже подтверждена.")
        return
    if status == 'not_paid':
        await callback.message.answer(f"⏳ Бронь #{bid} ещё не отмечена как оплаченная.")
        return
    if not ok:
        await callback.message.answer(f"❌ Не удалось подтвердить бронь #{bid}.")
        return

    # Берём расширенные данные (в т.ч. telegram_id арендодателя)
    b = db.get_booking_by_id(bid) or db.get_booking_full(bid)
    if b:
        # Сообщение клиенту (после подтверждения оплаты показываем номер места)
        try:
            await callback.bot.send_message(
                b.get('customer_telegram_id'),
                f"🎉 <b>Оплата подтверждена!</b>\n\n"
                f"🏠 {b.get('spot_number','')}\n"
                f"📍 {FIXED_ADDRESS}\n"
                f"📅 {format_datetime(b.get('start_time'))} — {format_datetime(b.get('end_time'))}",
                parse_mode="HTML"
            )
        except Exception:
            pass

        # Уведомление арендодателя (без контактов арендатора)
        try:
            sup_tid = b.get('supplier_telegram_id')
            if sup_tid:
                await callback.bot.send_message(
                    sup_tid,
                    f"✅ Ваш слот забронирован и оплачен!\n\n📅 {format_datetime(b.get('start_time'))} — {format_datetime(b.get('end_time'))}",
                    parse_mode="HTML"
                )
        except Exception:
            pass

    await callback.message.answer(f"✅ Бронь #{bid} подтверждена.")
@router.callback_query(F.data.startswith("adm_pay_decline_"))
async def admin_pay_decline(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bid = int(callback.data.replace("adm_pay_decline_", ""))
    ok = db.decline_payment(bid)
    b = db.get_booking_full(bid)
    if b:
        try:
            await callback.bot.send_message(
                b["customer_telegram_id"],
                f"❌ Оплата по брони #{bid} отклонена администратором.\n"
                f"Проверьте чек и отправьте снова."
            )
        except:
            pass
    await callback.message.answer("Готово." if ok else "Не удалось.")

@router.callback_query(F.data == "admin_export_excel")
async def admin_export_excel(callback: CallbackQuery):
    await callback.answer()
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        wb = Workbook()
        wb.remove(wb.active)

        def add_sheet(table_name: str):
            try:
                cur.execute(f"SELECT * FROM {table_name}")
                rows = cur.fetchall()
            except Exception:
                return
            ws = wb.create_sheet(title=table_name[:31])
            if not rows:
                ws.append(["(empty)"])
                return
            headers = rows[0].keys()
            ws.append(list(headers))
            for r in rows:
                ws.append([r[h] for h in headers])

        for tname in ("users", "parking_spots", "spot_availability", "bookings", "events_log"):
            add_sheet(tname)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp_path = tmp.name
        wb.save(tmp_path)

        file = FSInputFile(tmp_path)
        await callback.message.answer_document(file, caption="📊 Выгрузка в Excel (.xlsx)")

        try:
            os.remove(tmp_path)
        except Exception:
            pass
    except Exception as e:
        await callback.message.answer(f"Не удалось выгрузить Excel: {e}")
