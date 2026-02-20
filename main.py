import os
import asyncio
import time
import json
import io
import random
import redis.asyncio as redis
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Глобальные данные
accounts = {}       
start_times = {}    
last_status = {}    
notifications = {}  
disabled_users = {} 
global_disable = False
avatar_cache = {} 

status_chat_id = None
status_message_id = None

BSS_BG_URLS = [
    "https://i.ytimg.com/vi/6f5SleB_9uM/maxresdefault.jpg",
    "https://tr.rbxcdn.com/71231f2479e000418c39d962659e9c70/768/432/Image/Png"
]

# --- Утилиты ---
def safe_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def format_duration(seconds):
    seconds = int(seconds)
    d, h, m, s = seconds // 86400, (seconds % 86400) // 3600, (seconds % 3600) // 60, seconds % 60
    res = ""
    if d > 0: res += f"{d}d "
    if h > 0: res += f"{h}h "
    if m > 0: res += f"{m}m "
    res += f"{s}s"
    return res if res else "0s"

def get_user_id(message: types.Message):
    u = message.from_user
    return f"@{u.username}" if u.username else f"ID:{u.id}"

async def get_image_from_url(url):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return Image.open(io.BytesIO(data)).convert("RGBA")
    except: return None

async def get_roblox_avatar(username):
    if username in avatar_cache: return avatar_cache[username]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://users.roblox.com/v1/usernames/users", 
                                     json={"usernames": [username], "excludeBannedUsers": True}) as r:
                data = await r.json()
                u_id = data["data"][0]["id"]
            url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={u_id}&size=150x150&format=Png&isCircular=true"
            async with session.get(url) as r:
                data = await r.json()
                img_url = data["data"][0]["imageUrl"]
            async with session.get(img_url) as r:
                img_bytes = await r.read()
                img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                avatar_cache[username] = img
                return img
    except: return None

# --- БД (Redis) ---
async def init_db():
    global db, notifications, disabled_users, global_disable
    global accounts, start_times, status_chat_id, status_message_id
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            raw = await db.get("bss_v7_state")
            if raw:
                data = json.loads(raw)
                notifications.update(data.get("notifs", {}))
                disabled_users.update(data.get("disabled", {}))
                global_disable = data.get("global_disable", False)
                accounts.update(data.get("accounts", {}))
                start_times.update(data.get("start_times", {}))
                status_chat_id = data.get("chat_id")
                status_message_id = data.get("msg_id")
                print("✅ Данные восстановлены")
        except: pass

async def save_to_db():
    if db:
        try:
            payload = {
                "notifs": notifications, "disabled": disabled_users, "global_disable": global_disable,
                "accounts": accounts, "start_times": start_times,
                "chat_id": status_chat_id, "msg_id": status_message_id
            }
            await db.set("bss_v7_state", json.dumps(payload))
        except: pass

# --- Команды ---

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    global status_chat_id, status_message_id
    try: await message.delete()
    except: pass
    status_chat_id = message.chat.id
    msg = await bot.send_message(status_chat_id, "🐝 Запуск мониторинга...")
    status_message_id = msg.message_id
    try: await bot.pin_chat_message(status_chat_id, status_message_id, disable_notification=True)
    except: pass
    await save_to_db()

@dp.message(Command("add"))
async def add_cmd(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args: return await message.answer("Использование: /add Ник")
    rbx, target = args[0], args[1] if len(args) > 1 else get_user_id(message)
    if rbx not in notifications: notifications[rbx] = []
    if target not in notifications[rbx]: notifications[rbx].append(target)
    await save_to_db(); await message.answer(f"✅ Пинг добавлен для {rbx}")

@dp.message(Command("disable"))
async def disable_cmd(message: types.Message, command: CommandObject):
    global global_disable
    uid = get_user_id(message)
    arg = command.args.strip() if command.args else None
    if arg == "all": global_disable = True
    else: disabled_users[uid] = arg if arg else "all"
    await save_to_db(); await message.answer("🔇 Мут активен")

@dp.message(Command("enable"))
async def enable_cmd(message: types.Message):
    global global_disable
    uid = get_user_id(message); global_disable = False
    disabled_users.pop(uid, None)
    await save_to_db(); await message.answer("🔊 Мут снят")

# --- Логика ---

async def check_alerts(user, now):
    """Проверка и отправка уведомлений о вылете"""
    if user not in notifications or global_disable: return
    
    # Считаем длительность перед удалением
    dur = format_duration(now - float(start_times.get(user, now)))
    
    active_pings = []
    for member in notifications[user]:
        member_lower = member.lower()
        is_muted = False
        
        # Проверяем муты всех пользователей
        for m_uid, m_val in disabled_users.items():
            if m_uid.lower() == member_lower:
                if m_val == "all" or m_val.lower() == user.lower():
                    is_muted = True; break
        
        if not is_muted: active_pings.append(member)
    
    if active_pings:
        alert_text = f"⚠️ <b>{user}</b> ПОКИНУЛ ИГРУ!\n⏱ Время в сети: {dur}\n\n{' '.join(active_pings)}"
        try: await bot.send_message(status_chat_id, alert_text, parse_mode="HTML")
        except: pass

async def update_status_message():
    if not status_chat_id or not status_message_id: return
    now = time.time()
    
    if not accounts:
        text = "<b>📊 BSS Мониторинг</b>\nНет активных аккаунтов..."
    else:
        text = f"<b>📊 BSS Мониторинг</b>\n🕒 {time.strftime('%H:%M:%S')}\n\n"
        # Создаем копию ключей для безопасного удаления
        for user in list(accounts.keys()):
            last_seen = float(accounts[user])
            is_online = (now - last_seen) < 120
            
            # Если был онлайн, а теперь оффлайн — шлем алерт
            if last_status.get(user, False) and not is_online:
                await check_alerts(user, now)
                start_times.pop(user, None)
                accounts.pop(user, None)
                last_status[user] = False
                continue

            if is_online:
                last_status[user] = True
                if user not in start_times: start_times[user] = now
                text += f"🟢 <code>{safe_html(user)}</code> | {format_duration(now - float(start_times[user]))}\n"
            else:
                text += f"🔴 <code>{safe_html(user)}</code> | Offline\n"
    
    try: await bot.edit_message_text(text, status_chat_id, status_message_id, parse_mode="HTML")
    except: pass

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            u = data["username"]
            accounts[u], last_status[u] = time.time(), True
            if u not in start_times: start_times[u] = time.time()
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def main():
    await init_db()
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    asyncio.create_task(status_updater())
    await dp.start_polling(bot)

async def status_updater():
    while True:
        await update_status_message()
        await save_to_db()
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
