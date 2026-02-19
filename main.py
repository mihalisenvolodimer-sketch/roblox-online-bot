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
notifications = {} # {rbx_nick: [mentions]}
disabled_users = {} # {user_id: "all" or [rbx_nicks]}
global_disable = False

status_chat_id = None
status_message_id = None

def safe_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def get_user_id(message: types.Message):
    """Универсальный способ получить ID для мута"""
    u = message.from_user
    return f"@{u.username}" if u.username else f"ID:{u.id}"

def format_duration(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}ч {m}м {s}с" if h > 0 else f"{m}м {s}с" if m > 0 else f"{s}с"

async def init_db():
    global db, notifications, disabled_users, global_disable
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            data = await db.get("roblox_v5_data")
            if data:
                saved = json.loads(data)
                notifications.update(saved.get("notifs", {}))
                disabled_users.update(saved.get("disabled", {}))
                global_disable = saved.get("global_disable", False)
            print("✅ База данных загружена")
        except: print("❌ Ошибка БД")

async def save_to_db():
    if db:
        try:
            payload = {"notifs": notifications, "disabled": disabled_users, "global_disable": global_disable}
            await db.set("roblox_v5_data", json.dumps(payload))
        except: pass

@dp.message(Command("disable"))
async def disable_cmd(message: types.Message, command: CommandObject):
    global global_disable
    uid = get_user_id(message)
    arg = command.args.strip() if command.args else None
    
    if arg == "all":
        global_disable = True
        await message.answer("⚠️ <b>Глобальная пауза</b>: Пинги отключены для ВСЕХ.")
    elif not arg:
        disabled_users[uid] = "all"
        await message.answer("🔇 Ваши уведомления отключены для всех аккаунтов.")
    else:
        if uid not in disabled_users or disabled_users[uid] == "all":
            disabled_users[uid] = []
        if arg not in disabled_users[uid]:
            disabled_users[uid].append(arg)
        await message.answer(f"🔇 Ваши уведомления для <code>{safe_html(arg)}</code> отключены.")
    await save_to_db()

@dp.message(Command("enable"))
async def enable_cmd(message: types.Message, command: CommandObject):
    global global_disable
    uid = get_user_id(message)
    arg = command.args.strip() if command.args else None

    if arg == "all":
        global_disable = False
        await message.answer("🔊 Глобальные пинги снова включены.")
    else:
        if uid in disabled_users:
            del disabled_users[uid]
            await message.answer("🔊 Ваши уведомления снова включены.")
        else:
            await message.answer("У вас и так всё включено.")
    await save_to_db()

@dp.message(Command("list"))
async def list_notifications(message: types.Message):
    if not notifications:
        return await message.answer("Список пингов пуст.")
    
    status_header = "<b>🔔 Настройки пингов:</b>"
    if global_disable: status_header += " ⚠️ (ГЛОБАЛЬНАЯ ПАУЗА)"
    
    text = f"{status_header}\n\n"
    for rbx, users in notifications.items():
        if not users: continue
        formatted_users = []
        for u in users:
            # Проверяем, на паузе ли конкретный юзер для этого акка
            is_muted = False
            for d_uid, d_status in disabled_users.items():
                if d_uid in u: # если ID или ник совпадает
                    if d_status == "all" or rbx in d_status:
                        is_muted = True; break
            
            suffix = " 🔇" if is_muted else ""
            formatted_users.append(f"{u}{suffix}")
            
        text += f"• <code>{safe_html(rbx)}</code>: {', '.join(formatted_users)}\n"
    
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("add"))
async def add_notify(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args: return await message.answer("Использование: /add Nick @ping")
    
    rbx_name = args[0]
    mentions = args[1:] if len(args) > 1 else [get_user_id(message)]
    
    if rbx_name not in notifications: notifications[rbx_name] = []
    added = 0
    for m in mentions:
        if m not in notifications[rbx_name]:
            notifications[rbx_name].append(m)
            added += 1
    if added: await save_to_db()
    await message.answer(f"✅ Добавлено ({added}) для <code>{safe_html(rbx_name)}</code>", parse_mode="HTML")

@dp.message(Command("remove"))
async def remove_cmd(message: types.Message, command: CommandObject):
    uid = get_user_id(message)
    args = command.args.split() if command.args else []
    
    if not args:
        for rbx in notifications:
            notifications[rbx] = [m for m in notifications[rbx] if uid not in m]
        await message.answer("🗑 Вы удалены из всех списков.")
    else:
        rbx_name = args[0]
        target = args[1] if len(args) > 1 else uid
        if rbx_name in notifications:
            notifications[rbx_name] = [m for m in notifications[rbx_name] if target not in m]
            await message.answer(f"✅ Удален пинг {target} для {rbx_name}")
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
        p_label = " ❗(PAUSE)" if global_disable else ""
        text = f"<b>📊 Мониторинг Roblox</b>\n🕒 {time.strftime('%H:%M:%S')}{p_label}\n\n"
        for user in sorted(accounts.keys()):
            is_online = now - accounts[user] < 120
            if user in last_status and last_status[user] and not is_online:
                dur = format_duration(now - start_times.get(user, now))
                if user in notifications and not global_disable:
                    active = []
                    for m in notifications[user]:
                        muted = False
                        for d_uid, d_st in disabled_users.items():
                            if d_uid in m and (d_st == "all" or user in d_st):
                                muted = True; break
                        if not muted: active.append(m)
                    if active:
                        try: await bot.send_message(str(status_chat_id), f"⚠️ <b>{safe_html(user)}</b> ВЫЛЕТЕЛ!\n⏱ Был в сети: {dur}\n{' '.join(active)}", parse_mode="HTML")
                        except: pass
                start_times.pop(user, None)
            last_status[user] = is_online
            if is_online:
                if user not in start_times: start_times[user] = now
                text += f"🟢 <code>{safe_html(user)}</code> | ⏱ {format_duration(now - start_times[user])}\n"
            else: text += f"🔴 <code>{safe_html(user)}</code>\n"
    try: await bot.edit_message_text(text=text, chat_id=str(status_chat_id), message_id=status_message_id, parse_mode="HTML")
    except: pass

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            user = data["username"]
            accounts[user] = time.time()
            if user not in start_times: start_times[user] = time.time()
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
