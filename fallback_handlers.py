"""fallback_handlers.py

Отдельный Router с "catch-all" callback'ом.

Важно: этот router должен подключаться ПОСЛЕДНИМ (см. main.py),
иначе он будет перехватывать callback'и других роутеров (например, админ-панели).
"""

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

import database as db
from keyboards import get_main_menu_keyboard

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """Пустые/заглушечные кнопки из клавиатур."""
    await callback.answer()  # без текста, просто чтобы не крутилось


@router.callback_query()
async def fallback_any_callback(callback: CallbackQuery):
    """Catch-all: не оставляем Telegram-кнопки в состоянии "крутится".

    Вместо агрессивного show_alert отправляем пользователю обновлённое меню,
    чтобы он мог продолжить без /start.
    """
    try:
        await callback.answer()  # stop spinner
    except Exception:
        pass

    try:
        user = db.get_user_by_telegram_id(callback.from_user.id)
        is_admin = bool(user and user.get("role") == "admin")
        await callback.message.answer(
            "⚠️ Эта кнопка устарела. Я обновил меню.",
            reply_markup=get_main_menu_keyboard(is_admin),
        )
    except Exception as e:
        logger.debug(f"fallback callback handler error: {e}")
