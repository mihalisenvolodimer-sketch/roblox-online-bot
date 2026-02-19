import os
import asyncio
import time
import json
import io
import redis.asyncio as redis
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont

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
avatar_cache = {} # {username: ImageObject}

status_chat_id = None
status_message_id = None

# --- Утилиты ---
def safe_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def format_duration(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    res = ""
    if h > 0: res += f"{h}ч "
    if m > 0: res += f"{m}м "
    res += f"{s}с"
    return res if res else "0с"

def get_user_id(message: types.Message):
    u = message.from_user
    return f"@{u.username}" if u.username else f"ID:{u.id}"

async def get_roblox_avatar(username):
    """Получение аватарки игрока через API Roblox"""
    if username in avatar_cache: return avatar_cache[username]
    try:
        async with aiohttp.ClientSession() as session:
            # 1. ID по нику
            async with session.post("https://users.roblox.com/v1/usernames/users", 
                                     json={"usernames": [username], "excludeBannedUsers": True}) as r:
                data = await r.json()
                if not data.get("data"): return None
                u_id = data["data"][0]["id"]
            # 2. Ссылка на картинку
            url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={u_id}&size=150x150&format=Png&isCircular=true"
            async with session.get(url) as r:
                data = await r.json()
                img_url = data["data"][0]["imageUrl"]
            # 3. Загрузка
            async with session.get(img_url) as r:
                img_bytes = await r.read()
                img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                avatar_cache[username] = img
                return img
    except: return None

# --- БД ---
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

# --- Обработчики ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "<b>🤖 Roblox Monitor V5</b>\n\n"
        "• /ping — Создать таблицу\n"
        "• /list — Список подписок и мутов\n"
        "• /add [Ник] — Подписаться на уведомления\n"
        "• /disable [Ник/all] — Выключить звук\n"
        "• /enable — Включить звук\n"
        "• /img_create — Отчет с аватарками", parse_mode="HTML"
    )

@dp.message(Command("list"))
async def list_cmd(message: types.Message):
    if not notifications: return await message.answer("Список пуст.")
    header = "<b>🔔 Настройки пингов:</b>"
    if global_disable: header += " ⚠️ (ГЛОБАЛЬНАЯ ПАУЗА)"
    text = f"{header}\n\n"
    for rbx, users in notifications.items():
        if not users: continue
        fmt = []
        for u in users:
            is_muted = False
            u_l = u.lower().strip()
            for d_uid, d_st in disabled_users.items():
                d_l = d_uid.lower().strip()
                if (d_l in u_l or u_l in d_l) and (d_st == "all" or rbx in d_st):
                    is_muted = True; break
            fmt.append(f"{u}{' 🔇' if is_muted else ''}")
        text += f"• <code>{safe_html(rbx)}</code>: {', '.join(fmt)}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("img_create"))
async def img_create_cmd(message: types.Message):
    if not accounts: return await message.answer("Нет данных.")
    wait = await message.answer("⏳ Собираю аватарки и рисую...")
    try:
        width, height = 700, 150 + (len(accounts) * 65)
        img = Image.new('RGB', (width, height), color=(18, 18, 18))
        draw = ImageDraw.Draw(img)
        
        try: font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        except: font = ImageFont.load_default()

        draw.text((40, 30), f"ROBLOX MONITOR REPORT | {time.strftime('%H:%M')}", fill=(255, 255, 255), font=font)
        draw.line((40, 80, 660, 80), fill=(50, 50, 50), width=2)

        y, now = 110, time.time()
        for user in sorted(accounts.keys()):
            online = now - accounts[user] < 120
            bg = (35, 65, 35) if online else (40, 40, 40)
            draw.rounded_rectangle([40, y, 660, y+55], radius=10, fill=bg)

            avatar = await get_roblox_avatar(user)
            if avatar:
                avatar = avatar.resize((45, 45), Image.LANCZOS)
                img.paste(avatar, (50, y+5), avatar)
            else:
                draw.ellipse((50, y+10, 95, y+45), fill=(100, 100, 100))

            draw.text((110, y+15), user, fill=(255, 255, 255), font=font)
            status = f"Online: {format_duration(now - start_times[user])}" if online else "Offline"
            draw.text((420, y+15), status, fill=(200, 200, 200), font=font)
            y += 65

        buf = io.BytesIO(); img.save(buf, format='PNG'); buf.seek(0)
        await wait.delete()
        await message.answer_photo(BufferedInputFile(buf.read(), filename="res.png"))
    except Exception as e: await message.answer(f"Ошибка: {e}")

@dp.message(Command("add"))
async def add_cmd(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args: return await message.answer("Использование: /add Ник")
    rbx = args[0]
    mints = args[1:] if len(args) > 1 else [get_user_id(message)]
    if rbx not in notifications: notifications[rbx] = []
    for m in mints:
        if m not in notifications[rbx]: notifications[rbx].append(m)
    await save_to_db()
    await message.answer(f"✅ Пинги для <code>{rbx}</code> обновлены.")

@dp.message(Command("disable"))
async def disable_cmd(message: types.Message, command: CommandObject):
    global global_disable
    uid = get_user_id(message)
    arg = command.args.strip() if command.args else None
    if arg == "all": global_disable = True
    elif not arg: disabled_users[uid] = "all"
    else:
        if uid not in disabled_users or disabled_users[uid] == "all": disabled_users[uid] = []
        if arg not in disabled_users[uid]: disabled_users[uid].append(arg)
    await save_to_db(); await message.answer("🔇 Уведомления отключены.")

@dp.message(Command("enable"))
async def enable_cmd(message: types.Message):
    global global_disable
    uid = get_user_id(message)
    global_disable = False
    disabled_users.pop(uid, None)
    await save_to_db(); await message.answer("🔊 Уведомления включены.")

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    global status_chat_id, status_message_id
    try: await message.delete()
    except: pass
    status_chat_id = message.chat.id
    msg = await bot.send_message(chat_id=str(status_chat_id), text="⏳ Запуск...")
    status_message_id = msg.message_id
    try: await bot.pin_chat_message(chat_id=str(status_chat_id), message_id=status_message_id, disable_notification=True)
    except: pass

async def update_status_message():
    if not status_chat_id or not status_message_id: return
    now = time.time()
    if not accounts: text = "<b>📊 Мониторинг</b>\n⚠️ Ожидание сигналов..."
    else:
        text = f"<b>📊 Мониторинг Roblox</b>\n🕒 {time.strftime('%H:%M:%S')}{' ❗PAUSE' if global_disable else ''}\n\n"
        for user in sorted(accounts.keys()):
            online = now - accounts[user] < 120
            if user in last_status and last_status[user] and not online:
                dur = format_duration(now - start_times.get(user, now))
                if user in notifications and not global_disable:
                    active = []
                    for m in notifications[user]:
                        muted, m_l = False, m.lower()
                        for d_uid, d_st in disabled_users.items():
                            d_l = d_uid.lower()
                            if (d_l in m_l or m_l in d_l) and (d_st == "all" or user in d_st):
                                muted = True; break
                        if not muted: active.append(m)
                    if active:
                        try: await bot.send_message(str(status_chat_id), f"⚠️ <b>{user}</b> ВЫЛЕТЕЛ!\n⏱ Был: {dur}\n{' '.join(active)}", parse_mode="HTML")
                        except: pass
                start_times.pop(user, None)
            last_status[user] = online
            text += f"{'🟢' if online else '🔴'} <code>{safe_html(user)}</code>"
            if online:
                if user not in start_times: start_times[user] = now
                text += f" | ⏱ {format_duration(now - start_times[user])}"
            text += "\n"
    try: await bot.edit_message_text(text=text, chat_id=str(status_chat_id), message_id=status_message_id, parse_mode="HTML")
    except: pass

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            user = data["username"]
            accounts[user], last_status[user] = time.time(), True
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
