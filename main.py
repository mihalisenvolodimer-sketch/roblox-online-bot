import os, asyncio, time, json, redis.asyncio as redis, aiohttp
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

# Состояние в памяти (для скорости)
accounts = {}      
start_times = {}   
notifications = {} 
status_messages = {} # {chat_id: message_id}

# --- Работа с БД (Для выживания при перезагрузке) ---
async def load_data():
    global db, notifications, status_messages
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_STABLE_V25")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            print("✅ Данные Улья успешно подтянуты из базы")
    except Exception as e:
        print(f"⚠️ Ошибка БД: {e}")

async def save_data():
    if not db: return
    try:
        data = {"notifs": notifications, "msgs": status_messages}
        await db.set("BSS_STABLE_V25", json.dumps(data))
    except: pass

# --- Команды ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer(
        "<b>🐝 Улей BSS v25 (Стабильный)</b>\n\n"
        "/Information — Начать мониторинг (создает закреп)\n"
        "/add [Ник] — Пинг при вылете\n"
        "/list — Список подписок\n"
        "/Call — Общий сбор", parse_mode="HTML"
    )

@dp.message(Command("Information"))
async def cmd_info(m: types.Message):
    msg = await m.answer("<b>🐝 Инициализация Улья...</b>", parse_mode="HTML")
    status_messages[str(m.chat.id)] = msg.message_id
    try:
        await bot.pin_chat_message(m.chat.id, msg.message_id, disable_notification=True)
    except: pass
    await save_data()

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    if m.reply_to_message:
        rp = m.reply_to_message.from_user
        tag = f"@{rp.username}" if rp.username else f"ID:{rp.id}"

    if len(args) < 2: return await m.answer("Пример: /add Bubas")
    
    acc = args[1]
    if acc not in notifications: notifications[acc] = []
    if tag not in notifications[acc]: notifications[acc].append(tag)
    
    await save_data()
    await m.answer(f"✅ Пинг для <b>{acc}</b> добавлен!", parse_mode="HTML")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Список пингов пуст.")
    res = "📜 <b>Реестр подписок:</b>\n"
    for acc, tags in notifications.items():
        res += f"• {acc}: {', '.join(tags)}\n"
    await m.answer(res, parse_mode="HTML")

@dp.message(Command("Call"))
async def cmd_call(m: types.Message):
    tags = set()
    for t_list in notifications.values():
        for t in t_list: tags.add(t)
    if tags: await m.answer(f"📣 <b>ОБЩИЙ СБОР:</b>\n\n{' '.join(tags)}", parse_mode="HTML")

# --- Мониторинг ---

async def update_loop():
    while True:
        now = time.time()
        # Проверка вылетов (3 минуты)
        for user in list(accounts.keys()):
            if now - accounts[user] > 180:
                if user in notifications:
                    for cid in status_messages:
                        try: await bot.send_message(int(cid), f"🚨 <b>{user}</b> покинул Улей!\n{' '.join(notifications[user])}", parse_mode="HTML")
                        except: pass
                accounts.pop(user)
                start_times.pop(user, None)

        # Формирование текста
        text = f"<b>🐝 Состояние Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')}\n\n"
        if not accounts:
            text += "<i>Все пчелы спят (ожидание сигналов)...</i>"
        else:
            for u in sorted(accounts.keys()):
                dur = int(now - start_times.get(u, now))
                text += f"🟢 <code>{u}</code> | <b>{dur//3600}h {(dur%3600)//60}m {dur%60}s</b>\n"

        # Рассылка обновлений
        for cid, mid in list(status_messages.items()):
            try:
                await bot.edit_message_text(text, int(cid), int(mid), parse_mode="HTML")
            except: pass
        
        await save_data()
        await asyncio.sleep(25)

async def handle_signal(request):
    try:
        data = await request.json()
        u = data.get("username")
        if u:
            if u not in accounts: start_times[u] = time.time()
            accounts[u] = time.time()
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def main():
    await load_data()
    asyncio.create_task(update_loop())
    
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
