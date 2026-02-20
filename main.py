import os
import asyncio
import time
import json
import redis.asyncio as redis
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web

# --- Настройки ---
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
restart_count = 0
last_text = {} 

def logger(msg):
    print(f"DEBUG [{time.strftime('%H:%M:%S')}]: {msg}")

# --- База Данных ---
async def load_data():
    global db, notifications, status_messages, restart_count, start_times
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V37_FINAL_FIX")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            restart_count = data.get("restarts", 0) + 1
            saved_starts = data.get("starts", {})
            for k, v in saved_starts.items(): start_times[k] = float(v)
            logger(f"✅ Восстановлено. Рестарт: {restart_count}")
    except Exception as e:
        logger(f"Ошибка БД: {e}")

async def save_data():
    if not db: return
    try:
        data = {
            "notifs": notifications, 
            "msgs": status_messages, 
            "restarts": restart_count,
            "starts": start_times 
        }
        await db.set("BSS_V37_FINAL_FIX", json.dumps(data))
    except: pass

# --- Текст ---
def get_status_text():
    now = time.time()
    text = f"<b>🐝 Состояние Улья BSS</b>\n"
    text += f"🕒 {time.strftime('%H:%M:%S')} | 🔄 Рестартов: {restart_count}\n\n"
    if not accounts:
        text += "<i>Ожидание сигналов от макросов...</i>"
    else:
        for u in sorted(accounts.keys()):
            s_time = start_times.get(u, now)
            dur = int(now - s_time)
            h, m, s = dur//3600, (dur%3600)//60, dur%60
            text += f"🟢 <code>{u}</code> | <b>{h}ч {m}м {s}с</b>\n"
    return text

async def refresh_panels():
    text = get_status_text()
    for cid, mid in list(status_messages.items()):
        if last_text.get(str(cid)) == text: continue
        try:
            # ИСПОЛЬЗУЕМ ЯВНЫЕ ИМЕНА АРГУМЕНТОВ (chat_id=...), это лечит business_connection
            await bot.edit_message_text(
                chat_id=str(cid),
                message_id=int(mid),
                text=text,
                parse_mode="HTML"
            )
            last_text[str(cid)] = text
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger(f"Ошибка обновления {cid}: {e}")

# --- Команды ---

@dp.message(Command("start", ignore_case=True))
async def cmd_start(m: types.Message):
    await m.answer("<b>Улей v37</b>\n/information - панель\n/add [Ник] - пинг\n/remove [Ник] - удалить", parse_mode="HTML")

@dp.message(Command("information", ignore_case=True))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(chat_id=cid, message_id=status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML")
    status_messages[cid] = msg.message_id
    try:
        await bot.pin_chat_message(chat_id=cid, message_id=msg.message_id, disable_notification=True)
        await asyncio.sleep(1)
        await bot.delete_message(chat_id=cid, message_id=msg.message_id + 1)
    except: pass
    await save_data()

@dp.message(Command("add", ignore_case=True))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Ник?")
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    notifications.setdefault(acc, []).append(tag)
    await save_data()
    await m.answer(f"✅ Пинг для {acc} добавлен")

# Работает и /delete, и /remove
@dp.message(Command("delete", "remove", ignore_case=True))
async def cmd_remove(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Ник?")
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    if acc in notifications and tag in notifications[acc]:
        notifications[acc].remove(tag)
        if not notifications[acc]: del notifications[acc]
        await save_data()
        await m.answer(f"❌ Пинг для {acc} удален")
    else:
        await m.answer(f"Пинг для {acc} не найден")

@dp.message(Command("list", ignore_case=True))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Список пуст")
    res = "<b>📜 Подписки:</b>\n"
    for k, v in notifications.items(): res += f"• {k}: {', '.join(set(v))}\n"
    await m.answer(res, parse_mode="HTML")

# --- Сигналы ---

async def handle_signal(request):
    try:
        data = await request.json()
        u = data.get("username")
        if u:
            now = time.time()
            if u not in start_times: start_times[u] = now
            accounts[u] = now
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
                        try: await bot.send_message(chat_id=str(cid), text=f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{' '.join(notifications[u])}", parse_mode="HTML")
                        except: pass
                accounts.pop(u)
                start_times.pop(u, None)
        await refresh_panels()
        await save_data()
        await asyncio.sleep(30)

async def main():
    await load_data()
    asyncio.create_task(monitor())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    # Большая пауза, чтобы убить старого бота на хостинге
    await asyncio.sleep(5) 
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
