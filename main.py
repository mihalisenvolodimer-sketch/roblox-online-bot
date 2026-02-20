import os
import asyncio
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
    "https://static.wikia.nocookie.net/bee-swarm-simulator/images/a/a2/30_Bee_Zone.png/revision/latest?cb=20181223164917",
    "https://static.wikia.nocookie.net/bee-swarm-simulator/images/e/e4/BSS_Bamboo_Field.png/revision/latest?cb=20200403155737"
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
                if not data.get("data"): return None
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
            raw = await db.get("roblox_v6_full_state")
            if raw:
                data = json.loads(raw)
                notifications.update(data.get("notifs", {}))
                disabled_users.update(data.get("disabled", {}))
                global_disable = data.get("global_disable", False)
                # Восстановление состояния мониторинга
                accounts.update(data.get("accounts", {}))
                start_times.update(data.get("start_times", {}))
                status_chat_id = data.get("chat_id")
                status_message_id = data.get("msg_id")
                print("✅ Состояние успешно восстановлено из Redis")
        except Exception as e: print(f"❌ Ошибка БД: {e}")

async def save_to_db():
    if db:
        try:
            payload = {
                "notifs": notifications,
                "disabled": disabled_users,
                "global_disable": global_disable,
                "accounts": accounts,
                "start_times": start_times,
                "chat_id": status_chat_id,
                "msg_id": status_message_id
            }
            await db.set("roblox_v6_full_state", json.dumps(payload))
        except: pass

# --- Команды ---

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    global status_chat_id, status_message_id
    try: await message.delete()
    except: pass
    
    # Пытаемся удалить старый закреп перед созданием нового
    if status_chat_id and status_message_id:
        try: await bot.delete_message(status_chat_id, status_message_id)
        except: pass

    status_chat_id = message.chat.id
    msg = await bot.send_message(chat_id=status_chat_id, text="⏳ Инициализация мониторинга...")
    status_message_id = msg.message_id
    
    try: await bot.pin_chat_message(status_chat_id, status_message_id, disable_notification=True)
    except: pass
    await save_to_db()

@dp.message(Command("img_create"))
async def img_create_cmd(message: types.Message):
    if not accounts: return await message.answer("Нет данных.")
    wait = await message.answer("🎨 Рисую BSS отчет...")
    try:
        width, height = 700, 150 + (len(accounts) * 65)
        bg_img = await get_image_from_url(random.choice(BSS_BG_URLS))
        if not bg_img:
            bg_img = Image.new('RGBA', (width, height), (255, 193, 7, 255))
        else:
            bg_img = bg_img.resize((width, height), Image.LANCZOS)
            bg_img = ImageEnhance.Brightness(bg_img).enhance(0.4)

        draw = ImageDraw.Draw(bg_img)
        try: font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        except: font = ImageFont.load_default()

        draw.text((40, 30), f"BEE SWARM MONITOR | {time.strftime('%H:%M:%S')}", fill=(255, 255, 255), font=font)
        y, now = 110, time.time()
        for user in sorted(accounts.keys()):
            online = now - float(accounts[user]) < 120
            row_bg = (46, 125, 50, 180) if online else (40, 40, 40, 180)
            draw.rounded_rectangle([40, y, 660, y+55], radius=12, fill=row_bg)
            
            avatar = await get_roblox_avatar(user)
            if avatar:
                avatar = avatar.resize((45, 45), Image.LANCZOS)
                bg_img.paste(avatar, (50, y+5), avatar if avatar.mode == 'RGBA' else None)
            
            draw.text((110, y+15), user, fill=(255, 255, 255), font=font)
            dur = format_duration(now - float(start_times.get(user, now))) if online else "Offline"
            draw.text((420, y+15), f"Online: {dur}", fill=(255, 255, 255), font=font)
            y += 65

        final = bg_img.convert("RGB")
        buf = io.BytesIO(); final.save(buf, format='PNG'); buf.seek(0)
        await wait.delete()
        await message.answer_photo(BufferedInputFile(buf.read(), filename="bss.png"))
    except Exception as e: await message.answer(f"Ошибка: {e}")

@dp.message(Command("add"))
async def add_cmd(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args: return await message.answer("Использование: /add Ник")
    rbx, mints = args[0], args[1:] if len(args) > 1 else [get_user_id(message)]
    if rbx not in notifications: notifications[rbx] = []
    for m in mints:
        if m not in notifications[rbx]: notifications[rbx].append(m)
    await save_to_db(); await message.answer(f"✅ Добавлено: {rbx}")

@dp.message(Command("remove"))
async def remove_cmd(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    my_id = get_user_id(message).lower()
    if not args:
        for rbx in list(notifications.keys()):
            notifications[rbx] = [m for m in notifications[rbx] if m.lower() != my_id]
        await message.answer("🗑 Удалено из всех пингов.")
    elif len(args) == 1:
        rbx = args[0]
        if rbx in notifications:
            notifications[rbx] = [m for m in notifications[rbx] if m.lower() != my_id]
            await message.answer(f"✅ Удалено из {rbx}")
    await save_to_db()

@dp.message(Command("disable"))
async def disable_cmd(message: types.Message, command: CommandObject):
    global global_disable
    uid, arg = get_user_id(message), command.args.strip() if command.args else None
    if arg == "all": global_disable = True
    else: disabled_users[uid] = "all"
    await save_to_db(); await message.answer("🔇 Мут включен.")

@dp.message(Command("enable"))
async def enable_cmd(message: types.Message):
    global global_disable
    uid = get_user_id(message); global_disable = False
    disabled_users.pop(uid, None)
    await save_to_db(); await message.answer("🔊 Звук включен.")

# --- Логика обновления ---

async def update_status_message():
    if not status_chat_id or not status_message_id: return
    now = time.time()
    if not accounts: text = "<b>📊 Мониторинг</b>\nОжидание данных..."
    else:
        text = f"<b>📊 BSS Мониторинг</b>\n🕒 {time.strftime('%H:%M:%S')}\n\n"
        for user in sorted(accounts.keys()):
            online = now - float(accounts[user]) < 120
            if user in last_status and last_status[user] and not online:
                dur = format_duration(now - float(start_times.get(user, now)))
                if user in notifications and not global_disable:
                    active = [m for m in notifications[user] if not any(d_l in m.lower() and (d_st == "all" or user in d_st) for d_l, d_st in disabled_users.items())]
                    if active:
                        try: await bot.send_message(status_chat_id, f"⚠️ <b>{user}</b> ВЫЛЕТЕЛ!\n⏱ Был: {dur}\n{' '.join(active)}", parse_mode="HTML")
                        except: pass
                start_times.pop(user, None)
                accounts.pop(user, None)
            
            if online:
                last_status[user] = True
                if user not in start_times: start_times[user] = now
                text += f"🟢 <code>{safe_html(user)}</code> | ⏱ {format_duration(now - float(start_times[user]))}\n"
            else:
                last_status[user] = False
                text += f"🔴 <code>{safe_html(user)}</code> | Offline\n"
    
    try: await bot.edit_message_text(text=text, chat_id=status_chat_id, message_id=status_message_id, parse_mode="HTML")
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
        await update_status_message()
        await save_to_db() # Сохраняем состояние каждые 30 сек
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
