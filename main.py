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
last_sent_text = "" # Для предотвращения дублей

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

# --- БД ---
async def init_db():
    global db, notifications, disabled_users, global_disable
    global accounts, start_times, status_chat_id, status_message_id
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            raw = await db.get("bss_v10_state")
            if raw:
                data = json.loads(raw)
                notifications.update(data.get("notifs", {}))
                disabled_users.update(data.get("disabled", {}))
                global_disable = data.get("global_disable", False)
                accounts.update(data.get("accounts", {}))
                start_times.update(data.get("start_times", {}))
                status_chat_id = data.get("chat_id")
                status_message_id = data.get("msg_id")
                print(f"✅ Состояние восстановлено. Сообщение: {status_message_id}")
        except: pass

async def save_to_db():
    if db:
        try:
            payload = {
                "notifs": notifications, "disabled": disabled_users, "global_disable": global_disable,
                "accounts": accounts, "start_times": start_times,
                "chat_id": status_chat_id, "msg_id": status_message_id
            }
            await db.set("bss_v10_state", json.dumps(payload))
        except: pass

# --- Команды ---

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    global status_chat_id, status_message_id, last_sent_text
    try: await message.delete()
    except: pass
    
    if status_chat_id and status_message_id:
        try: await bot.delete_message(status_chat_id, status_message_id)
        except: pass

    status_chat_id = message.chat.id
    last_sent_text = "" # Сбрасываем кэш текста
    msg = await bot.send_message(status_chat_id, "<b>🐝 Запуск мониторинга...</b>", parse_mode="HTML")
    status_message_id = msg.message_id
    
    try: await bot.pin_chat_message(status_chat_id, status_message_id, disable_notification=True)
    except: pass
    await save_to_db()

# --- Логика обновления ---

async def update_status_message():
    global status_chat_id, status_message_id, last_sent_text
    if not status_chat_id or not status_message_id: return
    
    now = time.time()
    text = f"<b>📊 BSS Мониторинг</b>\n🕒 Обновлено: <code>{time.strftime('%H:%M:%S')}</code>\n\n"
    
    if not accounts:
        text += "<i>Жду данных от макросов...</i>"
    else:
        for user in sorted(list(accounts.keys())):
            last_seen = float(accounts[user])
            is_online = (now - last_seen) < 120
            
            if last_status.get(user, False) and not is_online:
                # Логика алертов (сокращено для надежности)
                dur = format_duration(now - float(start_times.get(user, now)))
                if user in notifications and not global_disable:
                    pings = " ".join(notifications[user])
                    try: await bot.send_message(status_chat_id, f"⚠️ <b>{user}</b> ВЫЛЕТЕЛ!\n⏱ Был в сети: {dur}\n{pings}", parse_mode="HTML")
                    except: pass
                start_times.pop(user, None)
                accounts.pop(user, None)
                last_status[user] = False
                continue

            if is_online:
                last_status[user] = True
                if user not in start_times: start_times[user] = now
                text += f"🟢 <code>{safe_html(user)}</code> | <b>{format_duration(now - float(start_times[user]))}</b>\n"
            else:
                text += f"🔴 <code>{safe_html(user)}</code> | Offline\n"

    # Если текст не изменился — не трогаем API Telegram
    if text == last_sent_text:
        return

    try:
        await bot.edit_message_text(
            text=text,
            chat_id=int(status_chat_id),
            message_id=int(status_message_id),
            parse_mode="HTML"
        )
        last_sent_text = text
    except Exception as e:
        if "message to edit not found" in str(e).lower():
            status_message_id = None
        elif "is not modified" in str(e).lower():
            pass # Игнорируем, если текст тот же самый

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
        await asyncio.sleep(20) # Обновляем чуть чаще (раз в 20 сек)

if __name__ == "__main__":
    asyncio.run(main())
