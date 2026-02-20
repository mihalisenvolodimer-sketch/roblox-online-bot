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

# Состояние
accounts = {}       
start_times = {}    
last_status = {}    
notifications = {}  
disabled_users = {} 
global_disable = False
avatar_cache = {} 

status_chat_id = None
status_message_id = None
last_sent_text = ""

BSS_BG_URLS = [
    "https://wallpapercave.com/wp/wp4746717.jpg",
    "https://wallpapercave.com/wp/wp4746732.jpg"
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
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return Image.open(io.BytesIO(await resp.read())).convert("RGBA")
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

# --- База Данных ---
async def init_db():
    global db, notifications, disabled_users, global_disable
    global accounts, start_times, status_chat_id, status_message_id
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            raw = await db.get("BSS_PERMANENT_DATA")
            if raw:
                data = json.loads(raw)
                notifications.update(data.get("notifs", {}))
                disabled_users.update(data.get("disabled", {}))
                global_disable = data.get("global_disable", False)
                accounts.update(data.get("accounts", {}))
                start_times.update(data.get("start_times", {}))
                status_chat_id = data.get("chat_id")
                status_message_id = data.get("msg_id")
                print(f"✅ База загружена. Чат: {status_chat_id}, Сообщение: {status_message_id}")
        except Exception as e:
            print(f"❌ Ошибка БД: {e}")

async def save_to_db():
    if db:
        try:
            payload = {
                "notifs": notifications, "disabled": disabled_users, "global_disable": global_disable,
                "accounts": accounts, "start_times": start_times,
                "chat_id": status_chat_id, "msg_id": status_message_id
            }
            await db.set("BSS_PERMANENT_DATA", json.dumps(payload))
        except: pass

# --- Команды ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("🐝 <b>Бот-мониторинг BSS</b>\n\n/ping - запустить/обновить панель\n/add Ник - добавить вас в пинг при вылете\n/list - кто подписан на уведомления\n/disable - мут уведомлений\n/enable - размут\n/img_create - отчет картинкой", parse_mode="HTML")

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    global status_chat_id, status_message_id, last_sent_text
    try: await message.delete()
    except: pass
    
    # Удаляем старое если есть
    if status_chat_id and status_message_id:
        try: await bot.delete_message(status_chat_id, status_message_id)
        except: pass

    status_chat_id = message.chat.id
    last_sent_text = "" 
    msg = await bot.send_message(status_chat_id, "<b>🐝 Запуск мониторинга...</b>", parse_mode="HTML")
    status_message_id = msg.message_id
    
    try: await bot.pin_chat_message(status_chat_id, status_message_id, disable_notification=True)
    except: pass
    await save_to_db()

@dp.message(Command("add"))
async def add_cmd(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args: return await message.answer("Укажите ник: /add Bubas")
    rbx, target = args[0], get_user_id(message)
    if rbx not in notifications: notifications[rbx] = []
    if target not in notifications[rbx]: notifications[rbx].append(target)
    await save_to_db(); await message.answer(f"✅ Пинг для <code>{rbx}</code> добавлен.", parse_mode="HTML")

@dp.message(Command("list"))
async def list_cmd(message: types.Message):
    if not notifications: return await message.answer("Список пингов пуст.")
    text = "<b>🔔 Список уведомлений:</b>\n"
    for rbx, users in notifications.items():
        if users: text += f"• <code>{rbx}</code>: {', '.join(users)}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("disable"))
async def disable_cmd(message: types.Message):
    uid = get_user_id(message)
    disabled_users[uid] = "all"
    await save_to_db(); await message.answer("🔇 Уведомления выключены.")

@dp.message(Command("enable"))
async def enable_cmd(message: types.Message):
    uid = get_user_id(message)
    disabled_users.pop(uid, None)
    await save_to_db(); await message.answer("🔊 Уведомления включены.")

@dp.message(Command("img_create"))
async def img_create_cmd(message: types.Message):
    if not accounts: return await message.answer("Нет данных для картинки.")
    wait = await message.answer("🖼 Рисую отчет...")
    try:
        width, height = 700, 150 + (max(1, len(accounts)) * 65)
        bg_img = await get_image_from_url(random.choice(BSS_BG_URLS))
        if not bg_img: bg_img = Image.new('RGBA', (width, height), (30, 30, 30, 255))
        else:
            bg_img = bg_img.resize((width, height), Image.LANCZOS)
            bg_img = ImageEnhance.Brightness(bg_img).enhance(0.4)
        draw = ImageDraw.Draw(bg_img)
        try: font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        except: font = ImageFont.load_default()
        
        draw.text((40, 30), f"BSS REPORT | {time.strftime('%H:%M:%S')}", fill=(255, 255, 255), font=font)
        y, now = 110, time.time()
        for user in sorted(accounts.keys()):
            online = now - float(accounts[user]) < 120
            row_bg = (46, 125, 50, 160) if online else (60, 60, 60, 160)
            draw.rounded_rectangle([40, y, 660, y+55], radius=12, fill=row_bg)
            avatar = await get_roblox_avatar(user)
            if avatar:
                avatar = avatar.resize((45, 45), Image.LANCZOS)
                bg_img.paste(avatar, (50, y+5), avatar if avatar.mode == 'RGBA' else None)
            
            dur = format_duration(now - float(start_times.get(user, now))) if online else "Offline"
            draw.text((110, y+15), f"{user} | {dur}", fill=(255, 255, 255), font=font)
            y += 65
        
        buf = io.BytesIO(); bg_img.convert("RGB").save(buf, format='PNG'); buf.seek(0)
        await wait.delete(); await message.answer_photo(BufferedInputFile(buf.read(), filename="bss.png"))
    except Exception as e: await message.answer(f"Ошибка картинки: {e}")

# --- Логика мониторинга ---

async def update_status_message():
    global status_chat_id, status_message_id, last_sent_text
    if not status_chat_id or not status_message_id: return
    
    now = time.time()
    text = f"<b>📊 BSS Мониторинг</b>\n🕒 Обновлено: <code>{time.strftime('%H:%M:%S')}</code>\n\n"
    
    if not accounts:
        text += "<i>Ожидание данных...</i>"
    else:
        for user in list(accounts.keys()):
            last_seen = float(accounts[user])
            is_online = (now - last_seen) < 120
            
            if last_status.get(user, False) and not is_online:
                if user in notifications:
                    pings = " ".join(notifications[user])
                    try: await bot.send_message(status_chat_id, f"⚠️ <b>{user}</b> ВЫЛЕТЕЛ!\n{pings}", parse_mode="HTML")
                    except: pass
                start_times.pop(user, None); accounts.pop(user, None); last_status[user] = False
                continue

            if is_online:
                last_status[user] = True
                if user not in start_times: start_times[user] = now
                text += f"🟢 <code>{safe_html(user)}</code> | <b>{format_duration(now - float(start_times[user]))}</b>\n"
            else:
                text += f"🔴 <code>{safe_html(user)}</code> | Offline\n"

    if text != last_sent_text:
        try:
            await bot.edit_message_text(text, int(status_chat_id), int(status_message_id), parse_mode="HTML")
            last_sent_text = text
        except Exception as e:
            # Если сообщение пропало — сбрасываем, апдейтер создаст новое в restore_monitoring
            if "message to edit not found" in str(e).lower():
                status_message_id = None

async def restore_monitoring():
    """Сценарий автоматического восстановления при перезагрузке"""
    global status_chat_id, status_message_id, last_sent_text
    await asyncio.sleep(5) # Даем боту прогрузиться
    if status_chat_id:
        print("🔄 Попытка восстановить мониторинг...")
        last_sent_text = ""
        # Если ID сообщения нет, или оно потеряно, создаем новое
        if not status_message_id:
            try:
                msg = await bot.send_message(status_chat_id, "<b>🐝 Мониторинг восстановлен после перезагрузки</b>", parse_mode="HTML")
                status_message_id = msg.message_id
                await save_to_db()
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
    
    # Запуск сервера сигналов
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    # Запуск фоновых задач
    asyncio.create_task(status_updater())
    asyncio.create_task(restore_monitoring()) # Авто-старт при включении
    
    # Старт бота
    await dp.start_polling(bot)

async def status_updater():
    while True:
        await update_status_message()
        await save_to_db()
        await asyncio.sleep(20)

if __name__ == "__main__":
    asyncio.run(main())
