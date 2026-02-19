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
start_times = {}   
last_status = {}    
notifications = {} 
disabled_users = {} 
global_disable = False

status_chat_id = None
status_message_id = None

def safe_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def format_duration(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}ч {m}м {s}с" if h > 0 else f"{m}м {s}с" if m > 0 else f"{s}с"

async def init_db():
    global db, notifications, disabled_users, global_disable
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            
            # 1. Пробуем загрузить новые данные (V5)
            data = await db.get("roblox_v5_data")
            if data:
                saved = json.loads(data)
                notifications.update(saved.get("notifs", {}))
                disabled_users.update(saved.get("disabled", {}))
                global_disable = saved.get("global_disable", False)
                print("✅ Данные V5 загружены")
            
            # 2. МИГРАЦИЯ: Если в V5 пусто, ищем в самых старых ключах
            if not notifications:
                old_keys = ["roblox_notifications", "roblox_v3_configs", "roblox_v4_data"]
                for key in old_keys:
                    old_raw = await db.get(key)
                    if old_raw:
                        old_data = json.loads(old_raw)
                        # Формат в старых ключах мог быть разным (словарь или список)
                        if isinstance(old_data, dict):
                            # Если это старый формат notifications (rbx: mention)
                            for k, v in old_data.items():
                                if k == "notifs": # если это v4 формат
                                    notifications.update(v)
                                else:
                                    if k not in notifications: notifications[k] = []
                                    if isinstance(v, list): notifications[k].extend(v)
                                    else: notifications[k].append(v)
                        print(f"🔄 Мигрированы данные из ключа: {key}")
                        await save_to_db() # Сразу сохраняем в новый формат
                        break
        except Exception as e:
            print(f"❌ Ошибка БД: {e}")

async def save_to_db():
    if db:
        try:
            payload = {
                "notifs": notifications, 
                "disabled": disabled_users,
                "global_disable": global_disable
            }
            await db.set("roblox_v5_data", json.dumps(payload))
        except: pass

@dp.message(Command("add"))
async def add_notify(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args:
        return await message.answer("Использование: <code>/add Nick @ping1 @ping2</code>", parse_mode="HTML")
    
    rbx_name = args[0]
    mentions_to_add = []

    if len(args) > 1:
        mentions_to_add = args[1:]
    elif message.reply_to_message:
        u = message.reply_to_message.from_user
        mentions_to_add.append(f"@{u.username}" if u.username else f"<a href='tg://user?id={u.id}'>{safe_html(u.full_name)}</a>")
    else:
        u = message.from_user
        mentions_to_add.append(f"@{u.username}" if u.username else f"<a href='tg://user?id={u.id}'>{safe_html(u.full_name)}</a>")

    if rbx_name not in notifications:
        notifications[rbx_name] = []
    
    added = 0
    for m in mentions_to_add:
        if m not in notifications[rbx_name]:
            notifications[rbx_name].append(m)
            added += 1
    
    if added > 0:
        await save_to_db()
        await message.answer(f"✅ Добавлено ({added}) для <code>{safe_html(rbx_name)}</code>", parse_mode="HTML")
    else:
        await message.answer("Пинги уже в списке.")

@dp.message(Command("list"))
async def list_notifications(message: types.Message):
    if not notifications:
        return await message.answer("Список пуст.")
    
    text = "<b>🔔 Настройки пингов:</b>\n\n"
    for rbx, users in notifications.items():
        if not users: continue
        text += f"• <code>{safe_html(rbx)}</code>: {', '.join(users)}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("remove"))
async def remove_cmd(message: types.Message, command: CommandObject):
    u = message.from_user
    my_mention = f"@{u.username}" if u.username else f"ID:{u.id}"
    args = command.args.split() if command.args else []
    
    if not args:
        # Удалить себя отовсюду
        for rbx in notifications:
            notifications[rbx] = [m for m in notifications[rbx] if my_mention not in m and str(u.id) not in m]
        await message.answer("🗑 Вы удалены из всех списков.")
    else:
        rbx_name = args[0]
        target = args[1] if len(args) > 1 else my_mention
        if rbx_name in notifications:
            notifications[rbx_name] = [m for m in notifications[rbx_name] if target not in m]
            await message.answer(f"✅ Пинг {target} удален для {rbx_name}")
        else:
            await message.answer("Аккаунт не найден.")
    await save_to_db()

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    global status_chat_id, status_message_id
    try: await message.delete()
    except: pass
    
    try:
        chat = await bot.get_chat(message.chat.id)
        if chat.pinned_message and chat.pinned_message.from_user.id == bot.id:
            await bot.delete_message(message.chat.id, chat.pinned_message.message_id)
    except: pass

    status_chat_id = message.chat.id
    msg = await bot.send_message(chat_id=str(status_chat_id), text="⏳ Сбор данных...")
    status_message_id = msg.message_id
    
    try:
        await bot.pin_chat_message(chat_id=str(status_chat_id), message_id=status_message_id, disable_notification=True)
        await bot.delete_message(chat_id=str(status_chat_id), message_id=status_message_id + 1)
    except: pass

async def update_status_message():
    if not status_chat_id or not status_message_id: return
    now = time.time()
    
    if not accounts:
        text = "<b>📊 Мониторинг</b>\n⚠️ Ожидание сигналов..."
    else:
        pause_label = " ❗(PAUSE)" if global_disable else ""
        text = f"<b>📊 Мониторинг Roblox</b>\n🕒 {time.strftime('%H:%M:%S')}{pause_label}\n\n"
        for user in sorted(accounts.keys()):
            is_online = now - accounts[user] < 120
            
            if user in last_status and last_status[user] and not is_online:
                duration = format_duration(now - start_times.get(user, now))
                if user in notifications and not global_disable:
                    # Умная фильтрация мутов
                    active_mentions = []
                    for m in notifications[user]:
                        is_muted = False
                        for uid, status in disabled_users.items():
                            if uid in m:
                                if status == "all" or user in status:
                                    is_muted = True; break
                        if not is_muted: active_mentions.append(m)
                    
                    if active_mentions:
                        try: await bot.send_message(str(status_chat_id), f"⚠️ <b>{safe_html(user)}</b> ВЫЛЕТЕЛ!\n⏱ Был в сети: {duration}\n{' '.join(active_mentions)}", parse_mode="HTML")
                        except: pass
                start_times.pop(user, None)

            last_status[user] = is_online
            if is_online:
                if user not in start_times: start_times[user] = now
                session = format_duration(now - start_times[user])
                text += f"🟢 <code>{safe_html(user)}</code> | ⏱ {session}\n"
            else:
                text += f"🔴 <code>{safe_html(user)}</code>\n"
    
    try: await bot.edit_message_text(text=text, chat_id=str(status_chat_id), message_id=status_message_id, parse_mode="HTML")
    except: pass

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            user = data["username"]
            accounts[user] = time.time()
            if user not in start_times: start_times[user] = time.time()
            asyncio.create_task(update_status_message())
            return web.Response(text="OK")
    except: pass
    return web.Response(text="Error", status=400)

async def main():
    await init_db()
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    asyncio.create_task(status_updater())
    await dp.start_polling(bot)

async def status_updater():
    while True:
        await update_status_message(); await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
