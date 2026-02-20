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
accounts = {}        
start_times = {}     
sessions = {}        
notifications = {}   
status_messages = {} 
known_chats = set()
avatar_cache = {}

# Список фонов (проверь, чтобы ссылки были прямыми на картинку)
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
    return f"{d}d {h}h {m}m {s%60}s" if d > 0 else f"{h}h {m}m {s%60}s"

async def get_image_bytes(url):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(url) as r:
                if r.status == 200:
                    return await r.read()
    except: return None

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
        raw = await db.get("BSS_V21_FINAL")
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
        await db.set("BSS_V21_FINAL", json.dumps(data))

# --- Команды ---

@dp.message(Command("start"))
async def start(m: types.Message):
    await m.answer(
        "<b>🐝 Улей BSS v21</b>\n\n"
        "📊 /Information — Новая панель (закреп)\n"
        "🔔 /add [Ник] [Пинги] — Настройка уведомлений\n"
        "❌ /remove [Ник/all] — Удалить подписки\n"
        "📜 /list — Все аккаунты и их пинги\n"
        "🖼 /img_create [Ник?] — Отчет/История\n"
        "📣 /Call — Пинг всех жителей", parse_mode="HTML"
    )

@dp.message(Command("Information"))
async def info(m: types.Message):
    cid = str(m.chat.id)
    known_chats.add(m.chat.id)
    
    if cid in status_messages:
        try: await bot.unpin_chat_message(m.chat.id, status_messages[cid])
        except: pass

    msg = await m.answer("<b>🐝 Инициализация Улья...</b>", parse_mode="HTML")
    status_messages[cid] = msg.message_id
    
    try:
        await bot.pin_chat_message(m.chat.id, msg.message_id, disable_notification=True)
        # Удаление сервисного сообщения о закрепе (id + 1)
        await asyncio.sleep(1)
        await bot.delete_message(m.chat.id, msg.message_id + 1)
    except: pass
    await save_db()
    await update_ui() # Сразу обновляем текст

@dp.message(Command("add"))
async def add(m: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    my_tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    if m.reply_to_message:
        r = m.reply_to_message.from_user
        my_tag = f"@{r.username}" if r.username else f"ID:{r.id}"

    if not args: return await m.answer("Пример: <code>/add Bubas @tag</code>", parse_mode="HTML")
    
    if args[0].lower() == "all":
        target_list = set(list(accounts.keys()) + list(notifications.keys()))
        for a in target_list:
            notifications.setdefault(a, [])
            if my_tag not in notifications[a]: notifications[a].append(my_tag)
    else:
        acc = args[0]
        tags = args[1:] if len(args) > 1 else [my_tag]
        notifications.setdefault(acc, [])
        for t in tags:
            if t not in notifications[acc]: notifications[acc].append(t)
    
    await save_db()
    await m.answer("✅ Подписки обновлены.")

@dp.message(Command("Call"))
async def call(m: types.Message):
    tags = set()
    for l in notifications.values():
        for t in l: tags.add(t)
    if not tags: return await m.answer("В Улье пока пусто.")
    await m.answer(f"📣 <b>ОБЩИЙ СБОР УЛЬЯ:</b>\n\n{' '.join(tags)}", parse_mode="HTML")

@dp.message(Command("list"))
async def list_cmd(m: types.Message):
    all_names = set(list(accounts.keys()) + list(notifications.keys()) + list(sessions.keys()))
    if not all_names: return await m.answer("Улей пуст.")
    
    text = "<b>📜 Реестр Улья:</b>\n\n"
    for name in all_names:
        pings = notifications.get(name, [])
        p_str = ", ".join(pings) if pings else "💨 Нет пингов"
        status = "🟢" if name in accounts else "🔴"
        text += f"{status} <code>{name}</code>\n└ Пинги: {p_str}\n\n"
    await m.answer(text, parse_mode="HTML")

@dp.message(Command("img_create"))
async def img_cmd(m: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    wait = await m.answer("🎨 Рисую картинку...")
    try:
        # Загрузка фона
        bg_data = await get_image_bytes(random.choice(BSS_BG_URLS))
        if bg_data:
            bg = Image.open(io.BytesIO(bg_data)).convert("RGBA")
        else:
            bg = Image.new("RGBA", (700, 500), (30, 30, 30, 255))

        if args: # Сессии
            user = args[0]
            data = sessions.get(user, [])
            canvas = bg.resize((700, 500), Image.LANCZOS)
            draw = ImageDraw.Draw(canvas)
            draw.text((40, 40), f"LOGS: {user.upper()}", fill="yellow")
            y = 100
            for s in data:
                txt = f"📅 {time.strftime('%d.%m %H:%M', time.localtime(s[0]))} | {format_dur(s[2])}"
                draw.text((40, y), txt, fill="white"); y += 45
            res = canvas
        else: # Онлайн
            h = max(300, 150 + (len(accounts) * 75))
            res = bg.resize((700, h), Image.LANCZOS)
            res = ImageEnhance.Brightness(res).enhance(0.3)
            draw = ImageDraw.Draw(res)
            y = 100
            for u in sorted(accounts.keys()):
                draw.rounded_rectangle([30, y, 670, y+65], radius=15, fill=(40,40,40,220))
                av = await get_avatar(u)
                if av: res.paste(av.resize((55,55)), (40, y+5), av.resize((55,55)))
                draw.text((110, y+20), f"{u} | {format_dur(time.time()-start_times[u])}", fill="white")
                y += 80
        
        bio = io.BytesIO(); res.convert("RGB").save(bio, "PNG"); bio.seek(0)
        await wait.delete(); await m.answer_photo(BufferedInputFile(bio.read(), filename="res.png"))
    except Exception as e: await m.answer(f"Ошибка графики: {e}")

# --- Логика Улья ---

async def update_ui():
    now = time.time()
    text = f"<b>🐝 Состояние Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')}\n\n"
    if not accounts: text += "<i>Все пчелы спят...</i>"
    else:
        for u in accounts:
            text += f"🟢 <code>{u}</code> | <b>{format_dur(now-start_times[u])}</b>\n"
    
    for cid, mid in list(status_messages.items()):
        try: await bot.edit_message_text(text, int(cid), int(mid), parse_mode="HTML")
        except: pass

async def monitor():
    while True:
        now = time.time()
        # Проверка вылетов
        for u in list(accounts.keys()):
            if (now - accounts[u]) > 170:
                st = start_times.pop(u, now)
                dur = now - st
                s_list = sessions.get(u, [])
                s_list.append([st, now, dur])
                sessions[u] = s_list[-7:]
                if u in notifications:
                    pings = " ".join(notifications[u])
                    for cid in status_messages:
                        try: await bot.send_message(int(cid), f"🔴 <b>{u}</b> ВЫЛЕТЕЛ!\n{pings}", parse_mode="HTML")
                        except: pass
                accounts.pop(u)
        
        await update_ui()
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
