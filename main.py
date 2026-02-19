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

# Ручная очистка текста для HTML, чтобы не зависеть от версий aiogram
def safe_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

async def init_db():
    global db, notifications
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            data = await db.get("roblox_v3_configs")
            if data:
                notifications.update(json.loads(data))
            print("✅ Redis Connected")
        except Exception as e:
            print(f"❌ Redis Error: {e}")

async def save_to_db():
    if db:
        await db.set("roblox_v3_configs", json.dumps(notifications))

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer("Бот запущен. Используйте /ping для мониторинга.")

@dp.message(Command("list"))
async def list_notifications(message: types.Message):
    if not notifications:
        return await message.answer("Список уведомлений пуст.")
    
    text = "<b>🔔 Список уведомлений:</b>\n\n"
    for rbx, users in notifications.items():
        mentions = ", ".join(users)
        text += f"• <code>{safe_html(rbx)}</code> — {mentions}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("add"))
async def add_notify(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args:
        return await message.answer("Использование: <code>/add Ник</code>", parse_mode="HTML")
    
    rbx_name = args[0]
    mention = None

    if len(args) > 1:
        mention = args[1]
    elif message.reply_to_message:
        user = message.reply_to_message.from_user
        mention = f"@{user.username}" if user.username else f"<a href='tg://user?id={user.id}'>{safe_html(user.full_name)}</a>"
    else:
        user = message.from_user
        mention = f"@{user.username}" if user.username else f"<a href='tg://user?id={user.id}'>{safe_html(user.full_name)}</a>"

    if rbx_name not in notifications:
        notifications[rbx_name] = []
    
    if mention not in notifications[rbx_name]:
        notifications[rbx_name].append(mention)
        await save_to_db()
        await message.answer(f"✅ Добавлен пинг для <code>{safe_html(rbx_name)}</code> юзеру {mention}", parse_mode="HTML")
    else:
        await message.answer("Этот юзер уже подписан на этот аккаунт.")

@dp.message(Command("remove"))
async def remove_notify(message: types.Message, command: CommandObject):
    if not command.args:
        return await message.answer("Укажите ник аккаунта.")
    
    rbx_name = command.args.strip()
    if rbx_name in notifications:
        del notifications[rbx_name]
        await save_to_db()
        await message.answer(f"❌ Все уведомления для <code>{safe_html(rbx_name)}</code> удалены.", parse_mode="HTML")
    else:
        await message.answer("Ник не найден.")

@dp.message(Command("delete"))
async def delete_bot_messages(message: types.Message):
    current_id = message.message_id
    for i in range(50):
        try: await bot.delete_message(message.chat.id, current_id - i)
        except: continue

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    try: await message.delete()
    except: pass
    
    global status_chat_id, status_message_id
    if status_chat_id and status_message_id:
        try: await bot.delete_message(status_chat_id, status_message_id)
        except: pass
            
    status_chat_id = message.chat.id
    msg = await bot.send_message(status_chat_id, "⏳ Инициализация таблицы...")
    status_message_id = msg.message_id
    
    try:
        await bot.pin_chat_message(status_chat_id, status_message_id, disable_notification=True)
        await asyncio.sleep(1)
        await bot.delete_message(status_chat_id, status_message_id + 1)
    except: pass

async def update_status_message():
    global status_message_id, status_chat_id
    if not status_chat_id or not status_message_id: return
        
    current_time = time.time()
    text = f"<b>📊 Мониторинг Roblox</b>\nОбновлено: {time.strftime('%H:%M:%S')}\n\n"
    
    for user in sorted(accounts.keys()):
        is_online = current_time - accounts[user] < 90
        
        if user in last_status and last_status[user] == True and not is_online:
            if user in notifications:
                mentions = " ".join(notifications[user])
                try:
                    await bot.send_message(status_chat_id, f"⚠️ <b>{safe_html(user)}</b> ВЫЛЕТЕЛ! {mentions}", parse_mode="HTML")
                except: pass
        
        last_status[user] = is_online
        status_icon = "🟢" if is_online else "🔴"
        text += f"{status_icon} <code>{safe_html(user)}</code>\n"
        
    try:
        await bot.edit_message_text(text, status_chat_id, status_message_id, parse_mode="HTML")
    except: pass

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            accounts[data["username"]] = time.time()
            return web.Response(text="OK")
    except: pass
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
