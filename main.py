import os, asyncio, time, json, io, random, redis.asyncio as redis, aiohttp
from aiogram import Bot, Dispatcher, types, F
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
sessions = {}        
notifications = {}   
status_messages = {} 
known_chats = set()

BSS_BG_URLS = [
    "https://wallpapercave.com/wp/wp4746717.jpg",
    "https://wallpaperaccess.com/full/2153575.jpg"
]

# --- Утилиты ---
def format_dur(seconds):
    s = int(float(seconds))
    h, m = (s % 86400) // 3600, (s % 3600) // 60
    return f"{s // 86400}d {h}h {m}m" if s >= 86400 else f"{h}h {m}m {s%60}s"

async def get_img_safe(url):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s:
            async with s.get(url) as r:
                if r.status == 200: return Image.open(io.BytesIO(await r.read())).convert("RGBA")
    except: pass
    return Image.new("RGBA", (700, 500), (40, 40, 40, 255))

# --- База Данных ---
async def load_db():
    global db, notifications, accounts, start_times, status_messages, known_chats, sessions
    if not REDIS_URL: return
    db = redis.from_url(REDIS_URL, decode_responses=True)
    raw = await db.get("BSS_V22_ULTIMATE")
    if raw:
        d = json.loads(raw)
        notifications.update(d.get("notifs", {}))
        accounts.update(d.get("accounts", {}))
        start_times.update(d.get("start_times", {}))
        status_messages.update(d.get("status_messages", {}))
        sessions.update(d.get("sessions", {}))
        known_chats = set(d.get("known_chats", []))

async def save_db():
    if db:
        data = {"notifs":notifications, "accounts":accounts, "start_times":start_times, 
                "status_messages":status_messages, "known_chats":list(known_chats), "sessions":sessions}
        await db.set("BSS_V22_ULTIMATE", json.dumps(data))

# --- Обработчики ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer("<b>🐝 Улей BSS v22</b>\n\n/Information — Создать панель\n/add [Ник] — Пинги\n/remove [Ник] — Удалить\n/list — Реестр\n/img_create — Графика\n/Call — Сбор", parse_mode="HTML")

@dp.message(Command("Information"))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    known_chats.add(m.chat.id)
    
    # Пытаемся открепить старое
    if cid in status_messages:
        try: await bot.unpin_chat_message(m.chat.id, status_messages[cid])
        except: pass

    msg = await m.answer("<b>🐝 Улей инициализирован!</b>", parse_mode="HTML")
    status_messages[cid] = msg.message_id
    
    try:
        await bot.pin_chat_message(m.chat.id, msg.message_id, disable_notification=True)
        # Удаление системного сообщения
        await asyncio.sleep(1)
        await bot.delete_message(m.chat.id, msg.message_id + 1)
    except: pass
    await save_db()

@dp.message(Command("add"))
async def cmd_add(m: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    # Определяем, кого тегать
    user_to_add = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    if m.reply_to_message:
        rp = m.reply_to_message.from_user
        user_to_add = f"@{rp.username}" if rp.username else f"ID:{rp.id}"

    if not args: return await m.answer("Использование: /add [Ник] или /add all")

    acc_target = args[0]
    if acc_target.lower() == "all":
        # Добавляем ко всем аккаунтам, которые когда-либо были в базе
        targets = set(list(accounts.keys()) + list(notifications.keys()))
        for a in targets:
            if a not in notifications: notifications[a] = []
            if user_to_add not in notifications[a]: notifications[a].append(user_to_add)
    else:
        if acc_target not in notifications: notifications[acc_target] = []
        # Если есть дополнительные аргументы — это кастомные пинги
        custom_pings = args[1:] if len(args) > 1 else [user_to_add]
        for p in custom_pings:
            if p not in notifications[acc_target]: notifications[acc_target].append(p)
    
    await save_db()
    await m.answer(f"✅ Пинги для <b>{acc_target}</b> обновлены.", parse_mode="HTML")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    all_names = set(list(accounts.keys()) + list(notifications.keys()) + list(sessions.keys()))
    if not all_names: return await m.answer("Улей пуст.")
    text = "<b>📜 Реестр Улья:</b>\n\n"
    for name in sorted(all_names):
        status = "🟢" if name in accounts else "🔴"
        p = notifications.get(name, [])
        text += f"{status} <code>{name}</code>\n└ Пинги: {', '.join(p) if p else 'Нет'}\n"
    await m.answer(text, parse_mode="HTML")

@dp.message(Command("Call"))
async def cmd_call(m: types.Message):
    tags = set()
    for plist in notifications.values():
        for t in plist: tags.add(t)
    if not tags: return await m.answer("Список пингов пуст.")
    await m.answer(f"📣 <b>ОБЩИЙ СБОР:</b>\n\n{' '.join(tags)}", parse_mode="HTML")

@dp.message(Command("img_create"))
async def cmd_img(m: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    wait = await m.answer("🖼 Рисую...")
    try:
        canvas = await get_img_safe(random.choice(BSS_BG_URLS))
        if args: # Сессии
            user = args[0]
            data = sessions.get(user, [])
            res = canvas.resize((700, 500))
            draw = ImageDraw.Draw(res)
            draw.text((30, 30), f"LOGS: {user}", fill="yellow")
            y = 80
            for s in data:
                draw.text((30, y), f"• {time.strftime('%H:%M', time.localtime(s[0]))} | {format_dur(s[2])}", fill="white")
                y += 40
        else: # Онлайн
            h = max(300, 100 + (len(accounts) * 70))
            res = canvas.resize((700, h))
            res = ImageEnhance.Brightness(res).enhance(0.4)
            draw = ImageDraw.Draw(res)
            y = 80
            for u in sorted(accounts.keys()):
                draw.rounded_rectangle([20, y, 680, y+60], radius=10, fill=(50,50,50,150))
                draw.text((40, y+15), f"{u} | {format_dur(time.time()-start_times[u])}", fill="white")
                y += 70
        
        bio = io.BytesIO(); res.convert("RGB").save(bio, "PNG"); bio.seek(0)
        await wait.delete(); await m.answer_photo(BufferedInputFile(bio.read(), filename="bss.png"))
    except Exception as e: await m.answer(f"Ошибка: {e}")

# --- Циклы ---

async def monitor():
    while True:
        now = time.time()
        for u in list(accounts.keys()):
            if (now - accounts[u]) > 180: # Вылет 3 мин
                st = start_times.pop(u, now)
                if u in notifications:
                    for cid in status_messages:
                        try: await bot.send_message(int(cid), f"🔴 <b>{u}</b> вылетел!\n{' '.join(notifications[u])}", parse_mode="HTML")
                        except: pass
                s_list = sessions.get(u, [])
                s_list.append([st, now, now - st])
                sessions[u] = s_list[-7:]
                accounts.pop(u)
        
        # Обновление UI
        text = f"<b>🐝 Состояние Улья</b>\n🕒 {time.strftime('%H:%M:%S')}\n\n"
        if not accounts: text += "<i>Все пчелы спят...</i>"
        else:
            for u in sorted(accounts.keys()):
                text += f"🟢 <code>{u}</code> | <b>{format_dur(now-start_times[u])}</b>\n"
        
        for cid, mid in list(status_messages.items()):
            try: await bot.edit_message_text(text, int(cid), int(mid), parse_mode="HTML")
            except: pass
        await save_db()
        await asyncio.sleep(30)

async def handle_signal(request):
    try:
        d = await request.json()
        u = d.get("username")
        if u:
            now = time.time()
            if u not in accounts: start_times[u] = now
            accounts[u] = now
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def main():
    await load_db()
    asyncio.create_task(monitor())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
