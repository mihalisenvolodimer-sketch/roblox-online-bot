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

# Функция для сброса и создания нового сообщения статуса
async def reset_status_msg(chat_id):
    global status_chat_id, status_message_id
    # Попробуем удалить старое сообщение, если оно было
    if status_chat_id and status_message_id:
        try:
            await bot.delete_message(status_chat_id, status_message_id)
        except:
            pass
    
    status_chat_id = chat_id
    status_message_id = None
    msg = await bot.send_message(chat_id, "⏳ Инициализация таблицы статусов...")
    status_message_id = msg.message_id

@dp.message(Command("start", "hello"))
async def hello_command(message: types.Message):
    await message.answer("Привет! Это бот для мониторинга Roblox. Используй /ping в группе, чтобы закрепить мониторинг.")

@dp.message(Command("ping"))
async def ping_command(message: types.Message):
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
        # Сортируем ники, чтобы список не прыгал
        sorted_users = sorted(accounts.keys())
        for user in sorted_users:
            last_seen = accounts[user]
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
        # Если сообщение удалили вручную — сбрасываем, чтобы создать новое при след. цикле
        if "message to edit not found" in str(e).lower():
            status_message_id = None
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
        await asyncio.sleep(15) # Чуть быстрее обновление

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
