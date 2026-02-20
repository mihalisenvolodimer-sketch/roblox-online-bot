import os, asyncio, time, json, io, random, redis.asyncio as redis, aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiohttp import web
from PIL import Image, ImageDraw

TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Состояние
accounts = {}        
start_times = {}     
notifications = {}   
status_messages = {} 
sessions = {}

# --- БД ---
async def sync_db(save=False):
    global db, notifications, accounts, start_times, status_messages, sessions
    if not REDIS_URL: return
    if not db: db = redis.from_url(REDIS_URL, decode_responses=True)
    
    if save:
        data = {"notifs": notifications, "accs": accounts, "starts": start_times, "msgs": status_messages, "sess": sessions}
        await db.set("BSS_V23_FINAL", json.dumps(data))
    else:
        raw = await db.get("BSS_V23_FINAL")
        if raw:
            d = json.loads(raw)
            notifications.update(d.get("notifs", {}))
            accounts.update(d.get("accs", {}))
            start_times.update(d.get("starts", {}))
            status_messages.update(d.get("msgs", {}))
            sessions.update(d.get("sess", {}))

# --- Команды ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer("🐝 <b>Улей BSS готов!</b>\n\n/Information - создать панель\n/add [Ник] - пинг при вылете\n/list - список всех\n/Call - позвать всех", parse_mode="HTML")

@dp.message(Command("Information"))
async def cmd_info(m: types.Message):
    msg = await m.answer("🐝 <b>Инициализация Улья...</b>", parse_mode="HTML")
    status_messages[str(m.chat.id)] = msg.message_id
    try:
        await bot.pin_chat_message(m.chat.id, msg.message_id, disable_notification=True)
    except: pass
    await sync_db(save=True)

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    if m.reply_to_message:
        tag = f"@{m.reply_to_message.from_user.username}" if m.reply_to_message.from_user.username else tag

    if len(args) < 2: return await m.answer("Напиши: /add Ник")
    
    acc = args[1]
    if acc.lower() == "all":
        target_list = set(list(accounts.keys()) + list(notifications.keys()))
        for a in target_list:
            if a not in notifications: notifications[a] = []
            if tag not in notifications[a]: notifications[a].append(tag)
    else:
        if acc not in notifications: notifications[acc] = []
        if tag not in notifications[acc]: notifications[acc].append(tag)
    
    await sync_db(save=True)
    await m.answer(f"✅ Пинги для <b>{acc}</b> настроены!", parse_mode="HTML")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    all_names = set(list(accounts.keys()) + list(notifications.keys()))
    if not all_names: return await m.answer("Улей пуст.")
    res = "📜 <b>Реестр:</b>\n\n"
    for n in all_names:
        status = "🟢" if n in accounts else "🔴"
        res += f"{status} <code>{n}</code> | Пинги: {', '.join(notifications.get(n, []))}\n"
    await m.answer(res, parse_mode="HTML")

@dp.message(Command("Call"))
async def cmd_call(m: types.Message):
    tags = set()
    for l in notifications.values():
        for t in l: tags.add(t)
    if not tags: return await m.answer("Пингов нет.")
    await m.answer(f"📣 <b>ОБЩИЙ СБОР:</b>\n\n{' '.join(tags)}", parse_mode="HTML")

@dp.message(Command("img_create"))
async def cmd_img(m: types.Message):
    if not accounts: return await m.answer("Нет никого в сети.")
    img = Image.new("RGB", (600, 400), (30, 30, 30))
    d = ImageDraw.Draw(img)
    y = 50
    d.text((50, 20), "BSS ONLINE REPORT", fill="yellow")
    for name in accounts:
        dur = int(time.time() - start_times.get(name, time.time()))
        d.text((50, y), f"- {name}: {dur//60} min in game", fill="white")
        y += 30
    bio = io.BytesIO(); img.save(bio, "PNG"); bio.seek(0)
    await m.answer_photo(BufferedInputFile(bio.read(), filename="bss.png"))

# --- Цикл мониторинга ---

async def monitor():
    while True:
        now = time.time()
        for u in list(accounts.keys()):
            if (now - accounts[u]) > 180: # 3 минуты тишины
                if u in notifications:
                    for cid in status_messages:
                        try: await bot.send_message(int(cid), f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{' '.join(notifications[u])}", parse_mode="HTML")
                        except: pass
                accounts.pop(u); start_times.pop(u, None)
        
        # Обновление закрепа
        text = f"<b>🐝 Состояние Улья</b>\n🕒 {time.strftime('%H:%M:%S')}\n\n"
        if not accounts: text += "<i>Все пчелы спят...</i>"
        else:
            for u in accounts:
                dur = int(now - start_times.get(u, now))
                text += f"🟢 <code>{u}</code> | <b>{dur//60}м {dur%60}с</b>\n"
        
        for cid, mid in list(status_messages.items()):
            try: await bot.edit_message_text(text, int(cid), int(mid), parse_mode="HTML")
            except: pass
        
        await sync_db(save=True)
        await asyncio.sleep(30)

async def handle_signal(request):
    try:
        data = await request.json()
        name = data.get("username")
        if name:
            if name not in accounts: start_times[name] = time.time()
            accounts[name] = time.time()
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def main():
    await sync_db()
    asyncio.create_task(monitor())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
