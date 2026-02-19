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

def safe_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

async def init_db():
    global db, notifications
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            # Пробуем оба ключа на всякий случай
            data = await db.get("roblox_notifications") or await db.get("roblox_v3_configs")
            if data:
                notifications.update(json.loads(data))
            print(f"✅ Redis Connected. Загружено записей: {len(notifications)}")
        except Exception as e:
            print(f"❌ Redis Error: {e}")

async def save_to_db():
    if db:
        try:
            await db.set("roblox_notifications", json.dumps(notifications))
        except Exception as e:
            print(f"❌ Save Error: {e}")

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer("Бот работает. Напиши /ping чтобы создать таблицу.")

@dp.message(Command("list"))
async def list_notifications(message: types.Message):
    if not notifications:
        return await message.answer("Список пуст.")
    text = "<b>🔔 Настройки пингов:</b>\n"
    for rbx, users in notifications.items():
        text += f"• <code>{safe_html(rbx)}</code>: {', '.join(users)}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("add"))
async def add_notify(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args:
        return await message.answer("Нужен ник: <code>/add Nick</code>", parse_mode="HTML")
    
    rbx_name = args[0]
    if len(args) > 1:
        mention = args[1]
    elif message.reply_to_message:
        u = message.reply_to_message.from_user
        mention = f"@{u.username}" if u.username else f"<a href='tg://user?id={u.id}'>{safe_html(u.full_name)}</a>"
    else:
        u = message.from_user
        mention = f"@{u.username}" if u.username else f"<a href='tg://user?id={u.id}'>{safe_html(u.full_name)}</a>"

    if rbx_name not in notifications: notifications[rbx_name] = []
    if mention not in notifications[rbx_name]:
        notifications[rbx_name].append(mention)
        await save_to_db()
        await message.answer(f"✅ Добавлен пинг {mention} для {safe_html(rbx_name)}", parse_mode="HTML")

@dp.message(Command("remove"))
async def remove_notify(message: types.Message, command: CommandObject):
    if not command.args: return await message.answer("Укажите ник.")
    rbx_name = command.args.strip()
    if rbx_name in notifications:
        del notifications[rbx_name]
        await save_to_db()
        await message.answer(f"❌ Удалены уведомления для {safe_html(rbx_name)}")

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    global status_chat_id, status_message_id
    try: await message.delete()
    except: pass
    
    if status_chat_id and status_message_id:
        try: await bot.delete_message(status_chat_id, status_message_id)
        except: pass
            
    status_chat_id = message.chat.id
    msg = await bot.send_message(status_chat_id, "⏳ Ожидание данных от Roblox...")
    status_message_id = msg.message_id
    try:
        await bot.pin_chat_message(status_chat_id, status_message_id, disable_notification=True)
        await asyncio.sleep(1)
        await bot.delete_message(status_chat_id, status_message_id + 1)
    except: pass

async def update_status_message():
    if not status_chat_id or not status_message_id: return
    current_time = time.time()
    
    if not accounts:
        text = "<b>📊 Мониторинг</b>\n⚠️ Нет данных. Запустите скрипт в Roblox."
    else:
        text = f"<b>📊 Мониторинг Roblox</b>\nОбновлено: {time.strftime('%H:%M:%S')}\n\n"
        for user in sorted(accounts.keys()):
            is_online = current_time - accounts[user] < 90
            if user in last_status and last_status[user] and not is_online:
                if user in notifications:
                    mentions = " ".join(notifications[user])
                    try: await bot.send_message(status_chat_id, f"⚠️ <b>{safe_html(user)}</b> вылетел! {mentions}", parse_mode="HTML")
                    except: pass
            last_status[user] = is_online
            text += f"{'🟢' if is_online else '🔴'} <code>{safe_html(user)}</code>\n"
        
    try:
        await bot.edit_message_text(text, status_chat_id, status_message_id, parse_mode="HTML")
    except: pass

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            user = data["username"]
            accounts[user] = time.time()
            print(f"📡 Сигнал получен: {user}") # Увидишь в логах Railway
            return web.Response(text="OK")
    except Exception as e:
        print(f"⚠️ Ошибка в сигнале: {e}")
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
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    asyncio.create_task(status_updater())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
