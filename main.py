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

# Данные мониторинга
accounts = {}
status_chat_id = None
status_message_id = None

@dp.message(CommandStart())
async def start_command(message: types.Message):
    global status_chat_id, status_message_id
    status_chat_id = message.chat.id
    status_message_id = None # Сбрасываем, чтобы создать новое сообщение в этом чате
    await message.answer("✅ Система мониторинга активирована в этом чате!\nОжидаю сигналы от Roblox...")

async def update_status_message():
    global status_message_id
    if not status_chat_id:
        return

    current_time = time.time()
    text = "📊 **Статус Roblox аккаунтов:**\n\n"
    
    if not accounts:
        text += "⏳ Ожидание первого сигнала от скрипта..."
    else:
        for user, last_seen in accounts.items():
            # Если сигнала не было больше 90 секунд — оффлайн
            is_online = current_time - last_seen < 90
            status = "🟢 В игре" if is_online else "🔴 Вылетел"
            text += f"👤 `{user}`: {status}\n"

    try:
        if status_message_id is None:
            # Отправляем новое сообщение
            msg = await bot.send_message(chat_id=status_chat_id, text=text, parse_mode="Markdown")
            status_message_id = msg.message_id
        else:
            # Редактируем существующее (используем именованные аргументы)
            await bot.edit_message_text(
                text=text,
                chat_id=status_chat_id,
                message_id=status_message_id,
                parse_mode="Markdown"
            )
    except Exception as e:
        print(f"Ошибка обновления: {e}")
        # Если сообщение удалили, сбрасываем ID, чтобы создать новое
        if "message to edit not found" in str(e).lower():
            status_message_id = None

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
        await asyncio.sleep(20) # Обновляем каждые 20 секунд

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
