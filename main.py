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

# Состояние Улья
accounts = {}        # Текущий онлайн {user: last_ts}
start_times = {}     # Время старта текущей сессии {user: ts}
sessions = {}        # История 7 сессий {user: [[start, end, dur], ...]}
notifications = {}   # Пинги {user: [pings]}
status_messages = {} # {chat_id: msg_id}
known_chats = set()
avatar_cache = {}

BSS_BG_URLS = [
    "https://wallpapercave.com/wp/wp4746717.jpg",
    "https://wallpaperaccess.com/full/2153575.jpg"
]

# --- Утилиты ---
def safe_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def format_dur(seconds):
    s = int(float(seconds))
    d, h, m = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
    res = f"{d}d " if d > 0 else ""
    res += f"{h}h " if h > 0 or d > 0 else ""
    res += f"{m}m {s%60}s"
    return res

async def get_image(url):
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r: return Image.open(io.BytesIO(await r.read()))

async def get_avatar(user):
    if user in avatar_cache: return avatar_cache[user]
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://users.roblox.com/v1/usernames/users", json={"usernames":[user],"excludeBannedUsers":True}) as r:
                uid = (await r.json())["data"][0]["id"]
            async with s.get(f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={uid}&size=150x150&format=Png&isCircular=true") as r:
                url = (await r.json())["data"][0]["imageUrl"]
            async with s.get(url) as r:
                img = Image.open(io.BytesIO(await r.read())).convert("RGBA")
                avatar_cache[user] = img
                return img
    except: return None

# --- БД ---
async def load_db():
    global db, notifications, accounts, start_times, status_messages, known_chats, sessions
    if REDIS_URL:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V20_PROD")
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
        data = {"notifs":notifications,"accounts":accounts,"start_times":start_times,
                "status_messages":status_messages,"known_chats":list(known_chats),"sessions":sessions}
        await db.set("BSS_V20_PROD", json.dumps(data))

# --- Команды ---

@dp.message(Command("start"))
async def start(m: types.Message):
    await m.answer(
        "<b>🐝 Улей BSS v20 (Stable)</b>\n\n"
        "📊 /Information — Создать/обновить панель (закреп)\n"
        "🔔 /add [Ник] [Пинги] — Настройка уведомлений\n"
        "🔔 /add all — Подписаться на все аккаунты\n"
        "❌ /remove [Ник/all] — Удалить подписки\n"
        "📜 /list — Все аккаунты и их пинги\n"
        "🖼 /img_create [Ник?] — Отчет или история сессий\n"
        "📣 /Call — Пинг всех жителей улья", parse_mode="HTML"
    )

@dp.message(Command("Information"))
async def info(m: types.Message):
    cid = str(m.chat.id)
    known_chats.add(m.chat.id)
    
    # Удаляем/открепляем старое
    if cid in status_messages:
        try: await bot.unpin_chat_message(m.chat.id, status_messages[cid])
        except: pass

    msg = await m.answer("<b>🐝 Заселение нового Улья...</b>", parse_mode="HTML")
    status_messages[cid] = msg.message_id
    
    try:
        pinned = await bot.pin_chat_message(m.chat.id, msg.message_id, disable_notification=True)
        # Пытаемся удалить сообщение "Бот закрепил сообщение"
        await bot.delete_message(m.chat.id, msg.message_id + 1)
    except: pass
    await save_db()

@dp.message(Command("add"))
async def add(m: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    my_tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    if m.reply_to_message:
        r = m.reply_to_message.from_user
        my_tag = f"@{r.username}" if r.username else f"ID:{r.id}"

    if not args: return await m.answer("Пример: <code>/add Bubas @myfriend</code>", parse_mode="HTML")
    
    if args[0].lower() == "all":
        target_accs = accounts.keys() if accounts else notifications.keys()
        for a in target_accs:
            notifications.setdefault(a, [])
            if my_tag not in notifications[a]: notifications[a].append(my_tag)
    else:
        acc = args[0]
        tags = args[1:] if len(args) > 1 else [my_tag]
        notifications.setdefault(acc, [])
        for t in tags:
            if t not in notifications[acc]: notifications[acc].append(t)
    
    await save_db()
    await m.answer("✅ Улей запомнил пинги.")

@dp.message(Command("Call"))
async def call(m: types.Message):
    tags = set()
    for l in notifications.values():
        for t in l: tags.add(t)
    if not tags: return await m.answer("В улье пока пусто.")
    await m.answer(f"📣 <b>ОБЩИЙ СБОР УЛЬЯ:</b>\n\n{' '.join(tags)}", parse_mode="HTML")

@dp.message(Command("img_create"))
async def img(m: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    wait = await m.answer("🎨 Рисую...")
    try:
        bg = await get_image(random.choice(BSS_BG_URLS))
        if args: # Сессии конкретного ника
            user = args[0]
            data = sessions.get(user, [])
            canvas = bg.resize((700, 500)).convert("RGBA")
            draw = ImageDraw.Draw(canvas)
            draw.text((40, 40), f"SESSION LOGS: {user.upper()}", fill="yellow")
            y = 100
            if not data: draw.text((40, 150), "No history yet", fill="white")
            for s in data:
                txt = f"📅 {time.strftime('%d.%m %H:%M', time.localtime(s[0]))} | Dur: {format_dur(s[2])}"
                draw.text((40, y), txt, fill="white"); y += 45
            res = canvas
        else: # Общий онлайн
            h = 150 + (len(accounts) * 70) if accounts else 300
            res = bg.resize((700, h)).convert("RGBA")
            res = ImageEnhance.Brightness(res).enhance(0.3)
            draw = ImageDraw.Draw(res)
            y = 100
            for u in sorted(accounts.keys()):
                draw.rounded_rectangle([30, y, 670, y+60], radius=15, fill=(40,40,40,200))
                av = await get_avatar(u)
                if av: res.paste(av.resize((50,50)), (40, y+5), av.resize((50,50)))
                draw.text((110, y+15), f"{u} | {format_dur(time.time()-start_times[u])}", fill="white")
                y += 75
        
        bio = io.BytesIO(); res.convert("RGB").save(bio, "PNG"); bio.seek(0)
        await wait.delete(); await m.answer_photo(BufferedInputFile(bio.read(), filename="res.png"))
    except Exception as e: await m.answer(f"Ошибка: {e}")

# --- Логика Улья ---

async def monitor():
    while True:
        now = time.time()
        for u in list(accounts.keys()):
            if (now - accounts[u]) > 160: # Вылет
                st = start_times.pop(u, now)
                dur = now - st
                # Запись в сессии
                s_list = sessions.get(u, [])
                s_list.append([st, now, dur])
                sessions[u] = s_list[-7:]
                # Уведомление
                if u in notifications:
                    for cid in status_messages:
                        try: await bot.send_message(int(cid), f"🔴 <b>{u}</b> ВЫЛЕТЕЛ!\n{' '.join(notifications[u])}", parse_mode="HTML")
                        except: pass
                accounts.pop(u)
        
        # Обновление текста панелей
        text = f"<b>🐝 Состояние Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')}\n\n"
        if not accounts: text += "<i>В сотах пусто...</i>"
        else:
            for u in accounts:
                text += f"🟢 <code>{u}</code> | <b>{format_dur(now-start_times[u])}</b>\n"
        
        for cid, mid in list(status_messages.items()):
            try: await bot.edit_message_text(text, int(cid), int(mid), parse_mode="HTML")
            except: pass
        
        await save_db()
        await asyncio.sleep(25)

async def handle_post(request):
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
    app = web.Application(); app.router.add_post('/signal', handle_post)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
