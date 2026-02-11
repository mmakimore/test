"""
ParkingBot - Telegram-бот для аренды парковочных мест
Точка входа приложения
"""
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

# python-dotenv is optional at runtime; BotHost usually provides env vars.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from config import APP_VERSION, BOT_TOKEN, LOG_LEVEL, LOG_FORMAT, DATABASE_PATH
import database as db
import os

# Создаём директорию для БД если нет
os.makedirs(os.path.dirname(DATABASE_PATH) or '.', exist_ok=True)
from user_handlers import router as user_router
from admin_handlers import router as admin_router
from fallback_handlers import router as fallback_router

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT
)
logger = logging.getLogger(__name__)

# Глобальная переменная для бота (для фоновых задач)
bot_instance: Bot = None


async def cleanup_old_data():
    """Очистка старых данных (бронирования старше 30 дней)"""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Помечаем старые бронирования как завершённые
            cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute('''
                UPDATE bookings 
                SET status = 'completed' 
                WHERE status = 'confirmed' AND end_time < ?
            ''', (cutoff,))
            
            # Удаляем старые слоты доступности
            cursor.execute('''
                DELETE FROM spot_availability 
                WHERE end_time < ? AND is_booked = 0
            ''', (cutoff,))
            
            # Деактивируем старые уведомления
            cursor.execute('''
                UPDATE spot_notifications 
                SET is_active = 0 
                WHERE desired_date < DATE('now', '-7 days')
            ''')
            
            logger.info("Old data cleanup completed")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


async def check_pending_bookings():
    """Проверка просроченных бронирований (не оплачены за 24 часа)"""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Находим бронирования старше 24 часов в статусе pending
            cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute('''
                SELECT b.id, b.availability_id, b.customer_id, b.spot_id,
                       u.telegram_id as customer_telegram_id,
                       ps.spot_number
                FROM bookings b
                JOIN users u ON b.customer_id = u.id
                JOIN parking_spots ps ON b.spot_id = ps.id
                WHERE b.status = 'pending' AND b.created_at < ?
            ''', (cutoff,))
            
            expired_bookings = cursor.fetchall()
            
            for booking in expired_bookings:
                # Отменяем бронирование
                cursor.execute('''
                    UPDATE bookings SET status = 'cancelled' WHERE id = ?
                ''', (booking['id'],))
                
                # Освобождаем слот
                cursor.execute('''
                    UPDATE spot_availability 
                    SET is_booked = 0, booked_by = NULL, booking_id = NULL
                    WHERE id = ?
                ''', (booking['availability_id'],))
                
                # Уведомляем пользователя
                if bot_instance:
                    try:
                        await bot_instance.send_message(
                            booking['customer_telegram_id'],
                            f"❌ <b>Бронирование отменено</b>\n\n"
                            f"Ваше бронирование места {booking['spot_number']} "
                            f"было автоматически отменено из-за отсутствия оплаты в течение 24 часов.",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify about expired booking: {e}")
            
            if expired_bookings:
                logger.info(f"Cancelled {len(expired_bookings)} expired bookings")
                
    except Exception as e:
        logger.error(f"Pending bookings check error: {e}")


async def send_booking_reminders():
    """Отправка напоминаний о предстоящих бронированиях (за 1 час)"""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Находим бронирования, которые начнутся через 1-2 часа
            now = datetime.now()
            in_1_hour = (now + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            in_2_hours = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            
            cursor.execute('''
                SELECT b.id, b.start_time, b.end_time, b.total_price,
                       u.telegram_id as customer_telegram_id,
                       ps.spot_number,
                       supplier.full_name as supplier_name
                FROM bookings b
                JOIN users u ON b.customer_id = u.id
                JOIN parking_spots ps ON b.spot_id = ps.id
                JOIN users supplier ON ps.supplier_id = supplier.id
                WHERE b.status = 'confirmed' 
                AND b.start_time BETWEEN ? AND ?
            ''', (in_1_hour, in_2_hours))
            
            upcoming = cursor.fetchall()
            
            for booking in upcoming:
                if bot_instance:
                    try:
                        start = datetime.fromisoformat(booking['start_time'])
                        await bot_instance.send_message(
                            booking['customer_telegram_id'],
                            f"⏰ <b>Напоминание!</b>\n\n"
                            f"Ваше бронирование места {booking['spot_number']} "
                            f"начнётся через ~1 час ({start.strftime('%H:%M')}).",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to send reminder: {e}")
                        
    except Exception as e:
        logger.error(f"Reminders error: {e}")


async def background_tasks():
    """Фоновые задачи"""
    while True:
        try:
            await asyncio.sleep(300)  # Каждые 5 минут
            
            await cleanup_old_data()
            await check_pending_bookings()
            await send_booking_reminders()
            
            # Авто-разбан
            unbanned = db.auto_unban_expired()
            if unbanned:
                logger.info(f"Auto-unbanned {unbanned} users")
            
        except asyncio.CancelledError:
            logger.info("Background tasks cancelled")
            break
        except Exception as e:
            logger.error(f"Background task error: {e}")
            await asyncio.sleep(60)


async def on_startup(bot: Bot):
    """Действия при запуске бота"""
    global bot_instance
    bot_instance = bot
    
    logger.info("Bot is starting...")
    
    # Инициализация БД
    db.init_database()
    logger.info("Database initialized")
    
    # Получаем информацию о боте
    bot_info = await bot.get_me()
    logger.info(f"Bot started: @{bot_info.username}")
    
    # Запускаем фоновые задачи
    asyncio.create_task(background_tasks())
    logger.info("Background tasks started")


async def on_shutdown(bot: Bot):
    """Действия при остановке бота"""
    logger.info("Bot is shutting down...")



async def expire_unpaid_loop(bot: Bot):
    """Каждую минуту истекаем неоплаченные брони (таймаут берём из config)."""
    from config import APP_VERSION, BOOKING_TIMEOUT_MINUTES
    while True:
        try:
            expired = db.expire_unpaid_bookings(BOOKING_TIMEOUT_MINUTES)
            for item in expired:
                bid = item['booking_id']
                tid = item['customer_telegram_id']
                try:
                    await bot.send_message(
                        tid,
                        f"⌛️ Бронь #{bid} истекла (не оплачено в течение {BOOKING_TIMEOUT_MINUTES} минут).\n"
                        f"Если нужно — создайте бронь заново."
                    )
                except:
                    pass
        except Exception as e:
            logger.error(f"expire loop: {e}")
        await asyncio.sleep(60)

async def main():
    # Инициализация БД до старта polling (на случай запуска без startup-hook)
    db.init_database()

    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    # Фоновая задача: истечение неоплаченных броней
    asyncio.create_task(expire_unpaid_loop(bot))
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Регистрируем роутеры
    # Важно: fallback_router ДОЛЖЕН быть последним, иначе он перехватит чужие callback'и.
    dp.include_router(admin_router)
    dp.include_router(user_router)
    dp.include_router(fallback_router)
    
    # Регистрируем хуки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    try:
        # Удаляем вебхук если был
        await bot.delete_webhook(drop_pending_updates=True)
        
        # Запускаем polling
        logger.info("Starting polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
