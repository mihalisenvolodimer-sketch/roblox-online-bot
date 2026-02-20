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
avatar_cache = {} 
known_chats = set()
status_messages = {} 
last_sent_text = ""

BSS_BG_URLS = ["https://wallpapercave.com/wp/wp4746717.jpg"]

# --- Утилиты ---
def safe_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def format_duration(seconds):
    try:
        seconds = int(float(seconds))
        d, h, m, s = seconds // 86400, (seconds % 86400) // 3600, (seconds % 3600) // 60, seconds % 60
        res = ""
        if d > 0: res += f"{d}d "
        if h > 0: res += f"{h}h "
        if m > 0: res += f"{m}m "
        res += f"{s}s"
        return res if res else "0s"
    except: return "0s"

async def get_image_from_url(url):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return Image.open(io.BytesIO(await resp.read())).convert("RGBA")
    except: return None

async def get_roblox_avatar(username):
    """Получение аватарки игрока из Roblox API"""
    if username in avatar_cache: return avatar_cache[username]
    try:
        async with aiohttp.ClientSession() as session:
            # 1. Получаем ID пользователя
            async with session.post("https://users.roblox.com/v1/usernames/users", 
                                     json={"usernames": [username], "excludeBannedUsers": True}) as r:
                data = await r.json()
                if not data.get("data"): return None
                u_id = data["data"][0]["id"]
            
            # 2. Получаем ссылку на аватар (голова)
            url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={u_id}&size=150x150&format=Png&isCircular=true"
            async with session.get(url) as r:
                data = await r.json()
                img_url = data["data"][0]["imageUrl"]
            
            # 3. Качаем картинку
            async with session.get(img_url) as r:
                img = Image.open(io.BytesIO(await r.read())).convert("RGBA")
                avatar_cache[username] = img
                return img
    except: return None

# --- База Данных ---
async def init_db():
    global db, notifications, accounts, start_times, status_messages, known_chats
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            raw = await db.get("BSS_PERM_V17")
            if raw:
                data = json.loads(raw)
                notifications.update(data.get("notifs", {}))
                accounts.update(data.get("accounts", {}))
                start_times.update(data.get("start_times", {}))
                status_messages.update(data.get("status_messages", {}))
                known_chats = set(data.get("known_chats", []))
        except: pass

async def save_to_db():
    if db:
        try:
            payload = {
                "notifs": notifications, "accounts": accounts, 
                "start_times": start_times, "status_messages": status_messages,
                "known_chats": list(known_chats)
            }
            await db.set("BSS_PERM_V17", json.dumps(payload))
        except: pass

# --- Команды ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    known_chats.add(message.chat.id)
    await save_to_db()
    text = (
        "<b>🐝 BSS Monitoring v17</b>\n\n"
        "/ping — Панель мониторинга\n"
        "/img_create — Отчет картинкой\n"
        "/add Ник — Пинг при вылете\n"
        "/list — Кто подписан"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    global last_sent_text
    known_chats.add(message.chat.id)
    try: await message.delete()
    except: pass
    
    cid = str(message.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(message.chat.id, status_messages[cid])
        except: pass

    last_sent_text = "" 
    msg = await bot.send_message(message.chat.id, "<b>🐝 Синхронизация данных...</b>", parse_mode="HTML")
    status_messages[cid] = msg.message_id
    
    try: await bot.pin_chat_message(message.chat.id, msg.message_id, disable_notification=True)
    except: pass
    await save_to_db()

@dp.message(Command("img_create"))
async def img_create_cmd(message: types.Message):
    if not accounts: return await message.answer("Нет активных игроков.")
    wait = await message.answer("🖼 Рисую отчет...")
    try:
        now = time.time()
        width, height = 700, 150 + (len(accounts) * 65)
        bg_img = await get_image_from_url(random.choice(BSS_BG_URLS))
        if not bg_img: bg_img = Image.new('RGBA', (width, height), (30, 30, 30, 255))
        else:
            bg_img = bg_img.resize((width, height), Image.LANCZOS)
            bg_img = ImageEnhance.Brightness(bg_img).enhance(0.4)
        
        draw = ImageDraw.Draw(bg_img)
        try: font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        except: font = ImageFont.load_default()
        
        draw.text((40, 30), f"BSS REPORT | {time.strftime('%H:%M:%S')}", fill=(255,255,255), font=font)
        y = 110
        for user in sorted(accounts.keys()):
            is_online = (now - float(accounts[user])) < 150
            row_bg = (46, 125, 50, 160) if is_online else (60, 60, 60, 160)
            draw.rounded_rectangle([40, y, 660, y+55], radius=12, fill=row_bg)
            
            # ОТРИСОВКА АВАТАРКИ
            avatar = await get_roblox_avatar(user)
            if avatar:
                avatar = avatar.resize((45, 45), Image.LANCZOS)
                bg_img.paste(avatar, (50, y+5), avatar if avatar.mode == 'RGBA' else None)
            
            st = float(start_times.get(user, now))
            dur = format_duration(now - st) if is_online else "Offline"
            draw.text((110, y+15), f"{user} | {dur}", fill=(255,255,255), font=font)
            y += 65
            
        buf = io.BytesIO(); bg_img.convert("RGB").save(buf, format='PNG'); buf.seek(0)
        await wait.delete(); await message.answer_photo(BufferedInputFile(buf.read(), filename="bss.png"))
    except Exception as e: await message.answer(f"Ошибка картинки: {e}")

# --- Логика Обновления ---

async def update_panels():
    global last_sent_text
    if not status_messages: return
    now = time.time()
    text = f"<b>📊 BSS Мониторинг</b>\n🕒 Обновлено: <code>{time.strftime('%H:%M:%S')}</code>\n\n"
    
    if not accounts:
        text += "<i>Ожидание макросов...</i>"
    else:
        for user in list(accounts.keys()):
            is_online = (now - float(accounts[user])) < 150
            if is_online:
                st = float(start_times.get(user, now))
                text += f"🟢 <code>{safe_html(user)}</code> | <b>{format_duration(now - st)}</b>\n"
                last_status[user] = True
            else:
                if last_status.get(user, False):
                    if user in notifications:
                        for cid in status_messages:
                            try: await bot.send_message(int(cid), f"⚠️ <b>{user}</b> ВЫЛЕТЕЛ!\n{' '.join(notifications[user])}", parse_mode="HTML")
                            except: pass
                    start_times.pop(user, None)
                    accounts.pop(user, None)
                    last_status[user] = False
                continue

    if text != last_sent_text:
        for cid, mid in list(status_messages.items()):
            try:
                await bot.edit_message_text(text=text, chat_id=int(cid), message_id=int(mid), parse_mode="HTML")
            except: pass
        last_sent_text = text

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            u, now = data["username"], time.time()
            accounts[u] = now
            if u not in start_times: start_times[u] = now
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def main():
    await init_db()
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    asyncio.create_task(status_updater())
    
    # Рестарт во всех чатах
    for cid in list(known_chats):
        try:
            msg = await bot.send_message(cid, "<b>♻️ Система восстановлена.</b>", parse_mode="HTML")
            status_messages[str(cid)] = msg.message_id
        except: pass

    await dp.start_polling(bot)

async def status_updater():
    while True:
        try:
            await update_panels()
            await save_to_db()
        except: pass
        await asyncio.sleep(20)

if __name__ == "__main__":
    asyncio.run(main())
