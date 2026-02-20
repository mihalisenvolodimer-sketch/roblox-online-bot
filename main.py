import os
import asyncio
import time
import json
import redis.asyncio as redis
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

accounts = {}      
start_times = {}   
notifications = {} 
status_messages = {}
last_text = {} # Чтобы не обновлять, если текст тот же

def logger(msg):
    print(f"DEBUG [{time.strftime('%H:%M:%S')}]: {msg}")

# --- База Данных ---
async def load_data():
    global db, notifications, status_messages
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V32_FINAL")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            logger("Данные подтянуты из базы")
    except Exception as e:
        logger(f"Ошибка БД: {e}")

async def save_data():
    if not db: return
    try:
        data = {"notifs": notifications, "msgs": status_messages}
        await db.set("BSS_V32_FINAL", json.dumps(data))
    except: pass

# --- Текст ---
def get_status_text():
    now = time.time()
    text = f"<b>🐝 Состояние Улья BSS</b>\n"
    text += f"🕒 Обновлено: {time.strftime('%H:%M:%S')}\n\n"
    if not accounts:
        text += "<i>Ожидание сигналов от макросов...</i>"
    else:
        for u in sorted(accounts.keys()):
            dur = int(now - start_times.get(u, now))
            h, m, s = dur//3600, (dur%3600)//60, dur%60
            text += f"🟢 <code>{u}</code> | <b>{h}ч {m}м {s}с</b>\n"
    return text

async def refresh_panels():
    if not status_messages: return
    text = get_status_text()
    for cid, mid in list(status_messages.items()):
        if last_text.get(cid) == text: continue # Пропуск если текст не изменился
        try:
            await bot.edit_message_text(text, int(cid), int(mid), parse_mode="HTML")
            last_text[cid] = text
        except Exception as e:
            if "message is not modified" not in str(e):
                logger(f"Ошибка обновления: {e}")

# --- Команды ---

@dp.message(Command("information", ignore_case=True))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    # Удаляем старое сообщение перед созданием нового
    if cid in status_messages:
        try: await bot.delete_message(m.chat.id, status_messages[cid])
        except: pass

    msg = await m.answer(get_status_text(), parse_mode="HTML")
    status_messages[cid] = msg.message_id
    try:
        await bot.pin_chat_message(m.chat.id, msg.message_id, disable_notification=True)
        await asyncio.sleep(1)
        await bot.delete_message(m.chat.id, msg.message_id + 1)
    except: pass
    await save_data()

@dp.message(Command("add", ignore_case=True))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    notifications.setdefault(acc, []).append(tag)
    await save_data()
    await m.answer(f"✅ Пинг для {acc} добавлен")

@dp.message(Command("list", ignore_case=True))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Список пуст.")
    res = "<b>📜 Подписки:</b>\n"
    for k, v in notifications.items(): res += f"• {k}: {', '.join(v)}\n"
    await m.answer(res, parse_mode="HTML")

# --- Сервер и Цикл ---

async def handle_signal(request):
    try:
        data = await request.json()
        u = data.get("username")
        if u:
            if u not in accounts: start_times[u] = time.time()
            accounts[u] = time.time()
            asyncio.create_task(refresh_panels())
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def monitor():
    while True:
        now = time.time()
        for u in list(accounts.keys()):
            if now - accounts[u] > 180:
                if u in notifications:
                    for cid in status_messages:
                        try: await bot.send_message(int(cid), f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{' '.join(notifications[u])}", parse_mode="HTML")
                        except: pass
                accounts.pop(u)
                start_times.pop(u, None)
        await refresh_panels()
        await save_data()
        await asyncio.sleep(30)

async def main():
    logger("Запуск системы...")
    await load_data()
    asyncio.create_task(monitor())
    
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    # Даем Railway время убить старые процессы
    await asyncio.sleep(2) 
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
