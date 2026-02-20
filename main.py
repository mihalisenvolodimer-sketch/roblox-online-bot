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

# --- База Данных ---
async def init_db():
    global db, notifications, accounts, start_times, status_messages, known_chats
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            raw = await db.get("BSS_PERM_V15")
            if raw:
                data = json.loads(raw)
                notifications.update(data.get("notifs", {}))
                accounts.update(data.get("accounts", {}))
                start_times.update(data.get("start_times", {}))
                status_messages.update(data.get("status_messages", {}))
                known_chats = set(data.get("known_chats", []))
                print(f"[DB] Данные загружены. Чатов в работе: {len(status_messages)}")
        except Exception as e: print(f"[DB] Ошибка загрузки: {e}")

async def save_to_db():
    if db:
        try:
            payload = {
                "notifs": notifications, "accounts": accounts, 
                "start_times": start_times, "status_messages": status_messages,
                "known_chats": list(known_chats)
            }
            await db.set("BSS_PERM_V15", json.dumps(payload))
        except Exception as e: print(f"[DB] Ошибка сохранения: {e}")

# --- Команды ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    known_chats.add(message.chat.id)
    await save_to_db()
    await message.answer("<b>🐝 BSS Monitoring v15</b>\n/ping — панель\n/add Ник — пинг", parse_mode="HTML")

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    global last_sent_text
    print(f"[CMD] /ping вызван в чате {message.chat.id}")
    known_chats.add(message.chat.id)
    try: await message.delete()
    except: pass
    
    cid = str(message.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(message.chat.id, status_messages[cid])
        except: pass

    last_sent_text = "" 
    msg = await bot.send_message(message.chat.id, "<b>🐝 Инициализация пасеки...</b>", parse_mode="HTML")
    status_messages[cid] = msg.message_id
    print(f"[CMD] Новое сообщение создано: {msg.message_id}")
    
    try: await bot.pin_chat_message(message.chat.id, msg.message_id, disable_notification=True)
    except: pass
    await save_to_db()

@dp.message(Command("add"))
async def add_cmd(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args: return await message.answer("Ник?")
    rbx, target = args[0], (f"@{message.from_user.username}" if message.from_user.username else f"ID:{message.from_user.id}")
    if rbx not in notifications: notifications[rbx] = []
    if target not in notifications[rbx]: notifications[rbx].append(target)
    await save_to_db(); await message.answer(f"✅ Пинг {rbx} добавлен")

# --- Основная логика обновления ---

async def update_panels():
    global last_sent_text
    if not status_messages:
        return

    now = time.time()
    text = f"<b>📊 BSS Мониторинг</b>\n🕒 Обновлено: <code>{time.strftime('%H:%M:%S')}</code>\n\n"
    
    active_count = 0
    if not accounts:
        text += "<i>Ожидание макросов...</i>"
    else:
        for user in list(accounts.keys()):
            is_online = (now - float(accounts[user])) < 120
            if is_online:
                active_count += 1
                st = float(start_times.get(user, now))
                text += f"🟢 <code>{safe_html(user)}</code> | <b>{format_duration(now - st)}</b>\n"
            else:
                # Логика вылета
                if last_status.get(user, False):
                    print(f"[LOG] {user} потерял соединение. Рассылка уведомлений.")
                    if user in notifications:
                        for cid in status_messages:
                            try: await bot.send_message(int(cid), f"⚠️ <b>{user}</b> ВЫЛЕТЕЛ!\n{' '.join(notifications[user])}", parse_mode="HTML")
                            except Exception as e: print(f"[ERR] Не удалось отправить алерт: {e}")
                    start_times.pop(user, None)
                    accounts.pop(user, None)
                    last_status[user] = False
                continue
            last_status[user] = True

    if text != last_sent_text:
        print(f"[LOOP] Обновление панелей. Активных аккаунтов: {active_count}")
        for cid, mid in list(status_messages.items()):
            try:
                await bot.edit_message_text(text, int(cid), int(mid), parse_mode="HTML")
            except Exception as e:
                print(f"[ERR] Ошибка обновления в {cid}: {e}")
                if "message to edit not found" in str(e).lower():
                    status_messages.pop(cid, None)
        last_sent_text = text

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            u, now = data["username"], time.time()
            accounts[u], last_status[u] = now, True
            if u not in start_times: start_times[u] = now
            print(f"[WEB] Сигнал от {u}")
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def main():
    await init_db()
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    asyncio.create_task(status_updater())
    
    # Авто-рассылка при старте
    if known_chats:
        print(f"[START] Восстановление мониторинга в {len(known_chats)} чатах")
        for cid in list(known_chats):
            try:
                msg = await bot.send_message(cid, "<b>♻️ Мониторинг перезапущен...</b>", parse_mode="HTML")
                status_messages[str(cid)] = msg.message_id
            except: pass
    
    await dp.start_polling(bot)

async def status_updater():
    print("[SYSTEM] Фоновый апдейтер запущен.")
    while True:
        try:
            await update_panels()
            await save_to_db()
        except Exception as e:
            print(f"[CRITICAL ERR] В апдейтере: {e}")
        await asyncio.sleep(20)

if __name__ == "__main__":
    asyncio.run(main())
