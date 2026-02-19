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
            # Настройка подключения к Redis
            db = redis.from_url(REDIS_URL, decode_responses=True)
            raw_data = await db.get("roblox_notifications")
            if raw_data:
                notifications.update(json.loads(raw_data))
                print(f"✅ Данные загружены. Аккаунтов: {len(notifications)}")
            
            # Тестовая запись для проверки связи
            await db.set("db_test", "ok")
            print("🚀 DATABASE TEST: OK")
        except Exception as e:
            print(f"❌ Ошибка Redis: {e}")
    else:
        print("⚠️ ВНИМАНИЕ: REDIS_URL не найден!")

async def save_to_db():
    if db:
        try:
            await db.set("roblox_notifications", json.dumps(notifications))
            print("💾 База данных синхронизирована")
        except Exception as e:
            print(f"❌ Ошибка сохранения: {e}")

@dp.message(Command("list"))
async def list_notifications(message: types.Message):
    if not notifications: return await message.answer("Список пуст.")
    text = "<b>🔔 Настройки пингов:</b>\n\n"
    for rbx, users in notifications.items():
        text += f"• <code>{safe_html(rbx)}</code>: {', '.join(users)}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("add"))
async def add_notify(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args:
        return await message.answer("Использование: <code>/add Nick @ping1 @ping2</code>", parse_mode="HTML")
    
    rbx_name = args[0]
    mentions_to_add = []

    # Если после ника есть еще аргументы - это пинги
    if len(args) > 1:
        mentions_to_add = args[1:]
    # Если аргументов нет, но есть реплай
    elif message.reply_to_message:
        u = message.reply_to_message.from_user
        mentions_to_add.append(f"@{u.username}" if u.username else f"<a href='tg://user?id={u.id}'>{safe_html(u.full_name)}</a>")
    # Если просто /add Nick
    else:
        u = message.from_user
        mentions_to_add.append(f"@{u.username}" if u.username else f"<a href='tg://user?id={u.id}'>{safe_html(u.full_name)}</a>")

    if rbx_name not in notifications:
        notifications[rbx_name] = []
    
    added_count = 0
    for m in mentions_to_add:
        if m not in notifications[rbx_name]:
            notifications[rbx_name].append(m)
            added_count += 1
    
    if added_count > 0:
        await save_to_db()
        await message.answer(f"✅ Добавлено пингов ({added_count}) для <code>{safe_html(rbx_name)}</code>", parse_mode="HTML")
    else:
        await message.answer("Эти пользователи уже были добавлены ранее.")

@dp.message(Command("remove"))
async def remove_notify(message: types.Message, command: CommandObject):
    if not command.args: return await message.answer("Укажите ник.")
    rbx_name = command.args.strip()
    if rbx_name in notifications:
        del notifications[rbx_name]
        await save_to_db()
        await message.answer(f"❌ Пинги для {safe_html(rbx_name)} удалены.")

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    global status_chat_id, status_message_id
    try: await message.delete()
    except: pass
    if status_chat_id and status_message_id:
        try: await bot.delete_message(chat_id=str(status_chat_id), message_id=status_message_id)
        except: pass
    status_chat_id = message.chat.id
    msg = await bot.send_message(chat_id=str(status_chat_id), text="⏳ Ожидание данных...")
    status_message_id = msg.message_id
    try:
        await bot.pin_chat_message(chat_id=str(status_chat_id), message_id=status_message_id, disable_notification=True)
        await asyncio.sleep(1)
        await bot.delete_message(chat_id=str(status_chat_id), message_id=status_message_id + 1)
    except: pass

async def update_status_message():
    if not status_chat_id or not status_message_id: return
    current_time = time.time()
    
    if not accounts:
        text = "<b>📊 Мониторинг</b>\n⚠️ Жду сигналов от Roblox..."
    else:
        text = f"<b>📊 Мониторинг Roblox</b>\nОбновлено: {time.strftime('%H:%M:%S')}\n\n"
        for user in sorted(accounts.keys()):
            is_online = current_time - accounts[user] < 120
            if user in last_status and last_status[user] and not is_online:
                if user in notifications:
                    mentions = " ".join(notifications[user])
                    try: 
                        await bot.send_message(chat_id=str(status_chat_id), text=f"⚠️ <b>{safe_html(user)}</b> вылетел! {mentions}", parse_mode="HTML")
                    except: pass
            last_status[user] = is_online
            text += f"{'🟢' if is_online else '🔴'} <code>{safe_html(user)}</code>\n"
    
    try:
        await bot.edit_message_text(text=text, chat_id=str(status_chat_id), message_id=status_message_id, parse_mode="HTML")
    except Exception as e:
        if "message is not modified" not in str(e): print(f"❌ Ошибка обновления: {e}")

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            user = data["username"]
            accounts[user] = time.time()
            asyncio.create_task(update_status_message())
            return web.Response(text="OK")
    except: pass
    return web.Response(text="Error", status=400)

async def status_updater():
    while True:
        await update_status_message()
        await asyncio.sleep(30)

async def main():
    await init_db()
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    asyncio.create_task(status_updater())
    print("🚀 Бот запущен и готов")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
