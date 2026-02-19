import os
import asyncio
import time
import json
import redis.asyncio as redis
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiohttp import web

TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

accounts = {}      
last_status = {}   
notifications = {} 
status_chat_id = None
status_message_id = None

async def init_db():
    global db, notifications
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            data = await db.get("roblox_notifications")
            if data:
                notifications.update(json.loads(data))
            print("✅ База данных Redis подключена")
        except Exception as e:
            print(f"❌ Ошибка Redis: {e}")

async def save_to_db():
    if db:
        try:
            await db.set("roblox_notifications", json.dumps(notifications))
        except Exception as e:
            print(f"❌ Ошибка сохранения в Redis: {e}")

@dp.message(Command("list"))
async def list_notifications(message: types.Message):
    if not notifications:
        return await message.answer("Список уведомлений пуст. Используйте /add")
    
    text = "🔔 **Активные уведомления:**\n\n"
    for rbx, tg in notifications.items():
        text += f"🔹 `{rbx}` — {tg}\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("add"))
async def add_notify(message: types.Message, command: CommandObject):
    if not command.args:
        return await message.answer("Использование: `/add Ник @юзер`", parse_mode="Markdown")
    args = command.args.split()
    if len(args) < 2: 
        return await message.answer("Укажите ник и тегните юзера!")
    rbx_name, mention = args[0], args[1]
    notifications[rbx_name] = mention
    await save_to_db()
    await message.answer(f"✅ Пинг для `{rbx_name}` сохранен: {mention}", parse_mode="Markdown")

@dp.message(Command("remove"))
async def remove_notify(message: types.Message, command: CommandObject):
    rbx_name = command.args
    if rbx_name in notifications:
        del notifications[rbx_name]
        await save_to_db()
        await message.answer(f"❌ Пинг для `{rbx_name}` удален.")
    else:
        await message.answer("Ник не найден.")

@dp.message(Command("delete"))
async def delete_bot_messages(message: types.Message):
    current_id = message.message_id
    deleted_count = 0
    # Проходим по последним 50 ID сообщений
    for i in range(50):
        try:
            await bot.delete_message(message.chat.id, current_id - i)
            deleted_count += 1
        except: 
            continue
    msg = await message.answer(f"🧹 Попытка очистки завершена. Удалено сообщений: {deleted_count}")
    await asyncio.sleep(3)
    try:
        await msg.delete()
    except:
        pass

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    
    global status_chat_id, status_message_id
    # Удаляем старую таблицу, если она была в памяти
    if status_chat_id and status_message_id:
        try:
            await bot.delete_message(status_chat_id, status_message_id)
        except:
            pass
            
    status_chat_id = message.chat.id
    msg = await bot.send_message(status_chat_id, "⏳ Запуск мониторинга...")
    status_message_id = msg.message_id
    
    try:
        await bot.pin_chat_message(status_chat_id, status_message_id, disable_notification=True)
        # Ждем секунду, чтобы появилось системное сообщение
        await asyncio.sleep(1)
        # Удаляем системное сообщение о закрепе (оно обычно на 1 ID больше)
        await bot.delete_message(status_chat_id, status_message_id + 1)
    except:
        pass

async def update_status_message():
    global status_message_id, status_chat_id
    if not status_chat_id or not status_message_id: 
        return
        
    current_time = time.time()
    text = "📊 **Мониторинг Roblox**\n"
    text += f"Обновлено: {time.strftime('%H:%M:%S')}\n\n"
    
    for user in sorted(accounts.keys()):
        is_online = current_time - accounts[user] < 90
        
        # Логика уведомления при смене статуса на оффлайн
        if user in last_status and last_status[user] == True and not is_online:
            if user in notifications:
                try:
                    await bot.send_message(status_chat_id, f"⚠️ **{user}** вылетел! {notifications[user]}", parse_mode="Markdown")
                except:
                    pass
        
        last_status[user] = is_online
        status_text = "🟢 В игре" if is_online else "🔴 Вылетел"
        text += f"👤 `{user}`: {status_text}\n"
        
    try:
        await bot.edit_message_text(text, status_chat_id, status_message_id, parse_mode="Markdown")
    except Exception:
        pass

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            accounts[data["username"]] = time.time()
            return web.Response(text="OK")
    except:
        pass
    return web.Response(text="Error", status=400)

async def status_updater():
    while True:
        await update_status_message()
        await asyncio.sleep(15)

async def main():
    await init_db()
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    asyncio.create_task(status_updater())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
