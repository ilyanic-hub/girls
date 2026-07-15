import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, CommandObject
import logging

BOT_TOKEN = "ТВОЙ_ТОКЕН_БОТА"
CHANNEL_ID = "@tvoi_kanal"  # Юзернейм твоего канала (бот должен быть админом в нём!)
DB_PATH = "local_database.db"  # Путь к твоей базе данных SQLite

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

async def check_channel_subscription(user_id: int) -> bool:
    """Проверяет, подписан ли пользователь на канал"""
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        # Если статус один из этих, значит пользователь подписан
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        return False

@dp.message(CommandStart())
async def handle_start(message: types.Message, command: CommandObject):
    code = command.args  # Извлекаем код авторизации (например: reg_1a2b3c)
    user_id = message.from_user.id
    username = message.from_user.username or f"id_{user_id}"

    if not code:
        await message.answer("Привет! Пожалуйста, используйте кнопку авторизации на нашем сайте.")
        return

    # 1. Проверяем подписку на канал
    is_subscribed = await check_channel_subscription(user_id)
    
    if not is_subscribed:
        await message.answer(
            f"❌ Для входа на сайт необходимо подписаться на наш канал!\n\n"
            f"1. Подпишитесь на {CHANNEL_ID}\n"
            f"2. После этого вернитесь в этот чат и снова нажмите кнопку «Старт»."
        )
        return

    # 2. Если подписан — обновляем сессию в БД сайта
    db = sqlite3.connect(DB_PATH)
    cursor = db.cursor()
    
    # Проверяем, существует ли такая сессия авторизации
    cursor.execute("SELECT code FROM telegram_auth_sessions WHERE code = ?", (code,))
    session_exists = cursor.fetchone()
    
    if session_exists:
        cursor.execute("""
            UPDATE telegram_auth_sessions 
            SET tg_user_id = ?, username = ?, status = 'success' 
            WHERE code = ?
        """, (user_id, username, code))
        db.commit()
        
        await message.answer("🎉 Успешно! Вы вошли в аккаунт. Возвращайтесь на вкладку сайта.")
    else:
        await message.answer("❌ Ссылка устарела или недействительна. Попробуйте войти с сайта заново.")
        
    db.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
