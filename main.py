import os
import asyncio
import time
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Данные мониторинга
accounts = {}
status_chat_id = None
status_message_id = None

async def reset_status_msg(chat_id):
    global status_chat_id, status_message_id
    
    # Пытаемся удалить старое сообщение перед созданием нового
    if status_chat_id and status_message_id:
        try:
            await bot.delete_message(status_chat_id, status_message_id)
        except:
            pass
    
    status_chat_id = chat_id
    
    # Отправляем новое сообщение
    msg = await bot.send_message(chat_id, "⏳ Инициализация таблицы статусов...")
    status_message_id = msg.message_id
    
    try:
        # Закрепляем сообщение
        await bot.pin_chat_message(chat_id, status_message_id, disable_notification=True)
        
        # Пытаемся найти и удалить системное сообщение о закрепе (оно обычно идет следующим)
        # Это работает не всегда идеально, но помогает очистить чат
    except Exception as e:
        print(f"Ошибка закрепления: {e}")

@dp.message(Command("start", "hello"))
async def hello_command(message: types.Message):
    # Команда работает, но мы не регистрируем её в BotFather, поэтому её нет в списке
    await message.answer("Бот работает. Используйте /ping для вывода мониторинга.")

@dp.message(Command("ping"))
async def ping_command(message: types.Message):
    # Удаляем само сообщение пользователя с командой /ping, чтобы было чисто
    try:
        await message.delete()
    except:
        pass
    await reset_status_msg(message.chat.id)

async def update_status_message():
    global status_message_id, status_chat_id
    if not status_chat_id or not status_message_id:
        return

    current_time = time.time()
    text = "📊 **Мониторинг Roblox Аккаунтов**\n"
    text += f"Последнее обновление: {time.strftime('%H:%M:%S')}\n\n"
    
    if not accounts:
        text += "⏳ Ожидание сигналов от скриптов..."
    else:
        sorted_users = sorted(accounts.keys())
        for user in sorted_users:
            last_seen = accounts[user]
            # Если сигнала нет 90 секунд — оффлайн
            is_online = current_time - last_seen < 90
            status = "🟢 В игре" if is_online else "🔴 Вылетел"
            text += f"👤 `{user}`: {status}\n"

    try:
        await bot.edit_message_text(
            text=text,
            chat_id=status_chat_id,
            message_id=status_message_id,
            parse_mode="Markdown"
        )
    except Exception as e:
        if "message to edit not found" in str(e).lower():
            status_message_id = None
        # Ошибку о том, что текст не изменился, игнорируем
        if "message is not modified" not in str(e).lower():
            print(f"Ошибка обновления: {e}")

async def handle_signal(request):
    try:
        data = await request.json()
        username = data.get("username")
        if username:
            accounts[username] = time.time()
            return web.Response(text="OK")
    except Exception as e:
        print(f"Ошибка API: {e}")
    return web.Response(text="Error", status=400)

async def status_updater():
    while True:
        await update_status_message()
        await asyncio.sleep(15)

async def main():
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    
    asyncio.create_task(site.start())
    asyncio.create_task(status_updater())
    
    print(f"Сервер запущен на порту {PORT}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
