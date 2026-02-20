import os, asyncio, time, json, redis.asyncio as redis, aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiohttp import web

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Состояние в памяти
accounts = {}      
start_times = {}   
notifications = {} 
status_messages = {}

# --- Работа с базой ---
async def load_data():
    global db, notifications, status_messages
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V29_STEADY")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
    except: pass

async def save_data():
    if not db: return
    try:
        data = {"notifs": notifications, "msgs": status_messages}
        await db.set("BSS_V29_STEADY", json.dumps(data))
    except: pass

# --- Утилиты ---
def get_status_text():
    now = time.time()
    text = f"<b>🐝 Состояние Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')}\n\n"
    if not accounts:
        text += "<i>Ожидание сигналов от макросов...</i>"
    else:
        for u in sorted(accounts.keys()):
            dur = int(now - start_times.get(u, now))
            h, m, s = dur//3600, (dur%3600)//60, dur%60
            text += f"🟢 <code>{u}</code> | <b>{h}ч {m}м {s}с</b>\n"
    return text

async def refresh_panels():
    text = get_status_text()
    for cid, mid in list(status_messages.items()):
        try:
            await bot.edit_message_text(text, int(cid), int(mid), parse_mode="HTML")
        except: pass

# --- Команды (ТЕПЕРЬ ЛЮБОЙ РЕГИСТР) ---

@dp.message(Command("start", ignore_case=True))
async def cmd_start(m: types.Message):
    await m.answer("<b>🐝 Бот v29 онлайн!</b>\n\n/information — создать панель\n/add [Ник] — пинг\n/call — сбор", parse_mode="HTML")

@dp.message(Command("information", ignore_case=True))
async def cmd_info(m: types.Message):
    msg = await m.answer(get_status_text(), parse_mode="HTML")
    status_messages[str(m.chat.id)] = msg.message_id
    try:
        await bot.pin_chat_message(m.chat.id, msg.message_id, disable_notification=True)
    except: pass
    await save_data()

@dp.message(Command("add", ignore_case=True))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    if acc not in notifications: notifications[acc] = []
    if tag not in notifications[acc]: notifications[acc].append(tag)
    await save_data()
    await m.answer(f"✅ Пинг для {acc} включен")

@dp.message(Command("call", ignore_case=True))
async def cmd_call(m: types.Message):
    tags = set()
    for t_list in notifications.values():
        for t in t_list: tags.add(t)
    if tags: await m.answer(f"📣 <b>ОБЩИЙ СБОР:</b>\n\n{' '.join(tags)}", parse_mode="HTML")

# --- Сигналы и Циклы ---

async def handle_signal(request):
    try:
        data = await request.json()
        u = data.get("username")
        if u:
            now = time.time()
            if u not in accounts: start_times[u] = now
            accounts[u] = now
            # Обновляем панель сразу при получении сигнала
            asyncio.create_task(refresh_panels())
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def monitor():
    while True:
        now = time.time()
        for u in list(accounts.keys()):
            if now - accounts[u] > 180: # 3 минуты тишины
                if u in notifications:
                    for cid in status_messages:
                        try: await bot.send_message(int(cid), f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{' '.join(notifications[u])}", parse_mode="HTML")
                        except: pass
                accounts.pop(u); start_times.pop(u, None)
        
        await refresh_panels()
        await save_data()
        await asyncio.sleep(30)

async def main():
    await load_data()
    asyncio.create_task(monitor())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    # Игнорируем старые сообщения и запускаем
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
