import os
import asyncio
import time
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiohttp import web

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Словарь для хранения аккаунтов { "username": last_seen_timestamp }
accounts = {}
# ID чата, где будет висеть статус (узнается после /start в группе)
status_chat_id = None
status_message_id = None

@dp.message(CommandStart())
async def start_command(message: types.Message):
    global status_chat_id
    status_chat_id = message.chat.id
    await message.answer("Система мониторинга запущена в этом чате!")

# Функция для обновления сообщения со статусом
async def update_status_message():
    global status_message_id
    if not status_chat_id: return

    text = "📊 **Статус Roblox аккаунтов:**\n\n"
    current_time = time.time()
    
    if not accounts:
        text += "Ожидание сигналов от аккаунтов..."
    else:
        for user, last_seen in accounts.items():
            # Если сигнала не было больше 90 секунд — аккаунт оффлайн
            status = "🟢 В игре" if current_time - last_seen < 90 else "🔴 Вылетел/Оффлайн"
            text += f"👤 {user}: {status}\n"

    try:
        if status_message_id is None:
            msg = await bot.send_message(status_chat_id, text, parse_mode="Markdown")
            status_message_id = msg.message_id
        else:
            await bot.edit_message_text(text, status_chat_id, status_message_id, parse_mode="Markdown")
    except Exception as e:
        print(f"Ошибка обновления: {e}")

# Обработчик сигналов от Roblox (API)
async def handle_signal(request):
    data = await request.json()
    username = data.get("username")
    if username:
        accounts[username] = time.time() # Обновляем время
        return web.Response(text="OK")
    return web.Response(text="Error", status=400)

# Фоновая задача для обновления статуса каждые 30 секунд
async def status_updater():
    while True:
        await update_status_message()
        await asyncio.sleep(30)

async def main():
    # Запуск веб-сервера для приема сигналов
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
