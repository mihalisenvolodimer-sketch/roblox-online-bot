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

# Глобальное состояние
accounts = {}        # {user: last_timestamp}
start_times = {}     # {user: start_timestamp}
sessions = {}        # {user: [[start, end, duration], ...]} (max 7)
notifications = {}   # {user: [ping1, ping2, ...]}
known_chats = set()
status_messages = {} # {chat_id: message_id}
avatar_cache = {}

BSS_BG_URLS = [
    "https://wallpapercave.com/wp/wp4746717.jpg",
    "https://wallpaperaccess.com/full/2153575.jpg"
]

# --- Утилиты ---
def safe_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def format_duration(seconds):
    try:
        s = int(float(seconds))
        d, h, m, s = s // 86400, (s % 86400) // 3600, (s % 3600) // 60, s % 60
        res = f"{d}d " if d > 0 else ""
        res += f"{h}h " if h > 0 or d > 0 else ""
        res += f"{m}m {s}s"
        return res
    except: return "0s"

async def get_roblox_avatar(username):
    if username in avatar_cache: return avatar_cache[username]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://users.roblox.com/v1/usernames/users", json={"usernames": [username], "excludeBannedUsers": True}) as r:
                u_id = (await r.json())["data"][0]["id"]
            url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={u_id}&size=150x150&format=Png&isCircular=true"
            async with session.get(url) as r:
                img_url = (await r.json())["data"][0]["imageUrl"]
            async with session.get(img_url) as r:
                img = Image.open(io.BytesIO(await r.read())).convert("RGBA")
                avatar_cache[username] = img
                return img
    except: return None

# --- Работа с БД ---
async def init_db():
    global db, notifications, accounts, start_times, status_messages, known_chats, sessions
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            raw = await db.get("BSS_ULTIMATE_V19")
            if raw:
                data = json.loads(raw)
                notifications.update(data.get("notifs", {}))
                accounts.update(data.get("accounts", {}))
                start_times.update(data.get("start_times", {}))
                status_messages.update(data.get("status_messages", {}))
                sessions.update(data.get("sessions", {}))
                known_chats = set(data.get("known_chats", []))
                print(f"[DB] Данные улья загружены. Чатов: {len(status_messages)}")
        except Exception as e: print(f"[DB] Ошибка: {e}")

async def save_to_db():
    if db:
        try:
            payload = {
                "notifs": notifications, "accounts": accounts, 
                "start_times": start_times, "status_messages": status_messages,
                "known_chats": list(known_chats), "sessions": sessions
            }
            await db.set("BSS_ULTIMATE_V19", json.dumps(payload))
        except: pass

# --- Команды ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    text = (
        "<b>🐝 Улей BSS: Команды</b>\n\n"
        "📊 <b>Мониторинг:</b>\n"
        "/Information — Создать новую панель и закрепить\n"
        "/ping — Обновить/найти текущую панель\n\n"
        "🔔 <b>Уведомления:</b>\n"
        "/add [Ник] [Пинги] — Добавить пинги на аккаунт\n"
        "/add all [Пинг] — На все текущие аки\n"
        "/add all all — Везде и всех (админ)\n"
        "/remove [Ник] — Удалить подписки\n"
        "/list — История всех аккаунтов и их пинги\n\n"
        "🖼 <b>Графика:</b>\n"
        "/img_create — Карточка онлайна\n"
        "/img_create [Ник] — История 7 сессий игрока\n\n"
        "📣 <b>Прочее:</b>\n"
        "/Call — Пинг всех участников улья"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("add"))
async def add_cmd(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    target_user = get_user_ping(message)
    
    # Логика Reply (если ответили на сообщение человека)
    if message.reply_to_message:
        target_user = get_user_ping(message.reply_to_message)

    if not args: return await message.answer("Использование: /add [Ник] [Пинги]")

    if args[0].lower() == "all":
        sub_args = args[1:] if len(args) > 1 else [target_user]
        # /add all all
        if len(args) > 1 and args[1].lower() == "all":
            for acc in accounts:
                notifications.setdefault(acc, [])
                if target_user not in notifications[acc]: notifications[acc].append(target_user)
            return await message.answer("✅ Добавлены везде.")
        # /add all [пинг]
        for acc in accounts:
            notifications.setdefault(acc, [])
            for p in sub_args:
                if p not in notifications[acc]: notifications[acc].append(p)
    else:
        acc = args[0]
        pings = args[1:] if len(args) > 1 else [target_user]
        notifications.setdefault(acc, [])
        for p in pings:
            if p not in notifications[acc]: notifications[acc].append(p)
    
    await save_to_db()
    await message.answer("✅ Настройки пингов обновлены.")

def get_user_ping(m):
    return f"@{m.from_user.username}" if m.from_user.username else f"<a href='tg://user?id={m.from_user.id}'>User</a>"

@dp.message(Command("remove"))
async def remove_cmd(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    target = get_user_ping(message)
    if not args: return await message.answer("/remove [Ник]")
    
    acc = args[0]
    if acc.lower() == "all":
        for a in notifications:
            if target in notifications[a]: notifications[a].remove(target)
    elif acc in notifications and target in notifications[acc]:
        notifications[acc].remove(target)
    
    await save_to_db()
    await message.answer(f"❌ Пинги для {acc} удалены.")

@dp.message(Command("list"))
async def list_cmd(message: types.Message):
    if not accounts and not sessions: return await message.answer("Улей пуст.")
    text = "<b>📜 Реестр Улья:</b>\n\n"
    all_names = set(list(accounts.keys()) + list(sessions.keys()))
    for name in all_names:
        last_seen = time.strftime('%H:%M', time.localtime(float(accounts.get(name, 0))))
        pings = notifications.get(name, [])
        p_str = ", ".join(pings) if pings else "💨 Нет пингов"
        text += f"👤 <code>{name}</code> | ЛОГ: {last_seen}\n└ Пинги: {p_str}\n\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("Information"))
async def info_cmd(message: types.Message):
    known_chats.add(message.chat.id)
    cid = str(message.chat.id)
    
    # Убираем старый закреп
    if cid in status_messages:
        try: await bot.unpin_chat_message(message.chat.id, status_messages[cid])
        except: pass

    msg = await bot.send_message(message.chat.id, "<b>🐝 Заселение нового Улья...</b>", parse_mode="HTML")
    status_messages[cid] = msg.message_id
    
    try: 
        await bot.pin_chat_message(message.chat.id, msg.message_id, disable_notification=True)
        # Удаление сервисного сообщения "закрепил сообщение"
        # Обычно это сообщение с message_id + 1, но надежнее ловить его через контент
    except: pass
    await save_to_db()

@dp.message(Command("Call"))
async def call_cmd(message: types.Message):
    all_pings = set()
    for plist in notifications.values():
        for p in plist: all_pings.add(p)
    if not all_pings: return await message.answer("Никто не настроил пинги.")
    await message.answer(f"📣 <b>ОБЩИЙ СБОР УЛЬЯ:</b>\n{ ' '.join(all_pings) }", parse_mode="HTML")

@dp.message(Command("img_create"))
async def img_create_cmd(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    wait = await message.answer("🖼 Рисую...")
    try:
        now = time.time()
        bg_url = random.choice(BSS_BG_URLS)
        
        if args: # Логика сессий конкретного игрока
            user = args[0]
            user_sessions = sessions.get(user, [])
            width, height = 700, 500
            img = await generate_session_img(user, user_sessions, bg_url)
        else: # Общий онлайн
            width, height = 700, 150 + (len(accounts) * 65)
            img = await generate_online_img(accounts, start_times, bg_url)
            
        buf = io.BytesIO(); img.convert("RGB").save(buf, format='PNG'); buf.seek(0)
        await wait.delete(); await message.answer_photo(BufferedInputFile(buf.read(), filename="bss.png"))
    except Exception as e: await message.answer(f"Ошибка: {e}")

# --- Фоновые процессы ---

async def update_loop():
    while True:
        now = time.time()
        # Проверка вылетов
        for user in list(accounts.keys()):
            last_val = float(accounts[user])
            if (now - last_val) > 150: # Вылетел (2.5 минуты тишины)
                if user in notifications:
                    pings = " ".join(notifications[user])
                    for cid in status_messages:
                        try: await bot.send_message(int(cid), f"🔴 <b>{user}</b> покинул Улей!\n{pings}", parse_mode="HTML")
                        except: pass
                
                # Сохраняем сессию
                duration = now - float(start_times.get(user, now))
                s_list = sessions.get(user, [])
                s_list.append([start_times.get(user, now), now, duration])
                sessions[user] = s_list[-7:] # Только 7 последних
                
                accounts.pop(user, None)
                start_times.pop(user, None)

        # Редактирование панелей
        text = f"<b>🐝 Состояние Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')}\n\n"
        if not accounts: text += "<i>Все пчелы спят...</i>"
        else:
            for u in accounts:
                dur = format_duration(now - float(start_times.get(u, now)))
                text += f"🟢 <code>{u}</code> | <b>{dur}</b>\n"
        
        for cid, mid in list(status_messages.items()):
            try: await bot.edit_message_text(text, int(cid), int(mid), parse_mode="HTML")
            except: pass
        
        await save_to_db()
        await asyncio.sleep(25)

async def handle_signal(request):
    try:
        data = await request.json()
        u = data.get("username")
        if u:
            now = time.time()
            if u not in accounts: start_times[u] = now
            accounts[u] = now
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

# --- Рисование ---

async def generate_online_img(accs, starts, bg_url):
    now = time.time()
    h = 150 + (len(accs) * 70)
    base = await get_image_from_url(bg_url)
    base = base.resize((700, h)).convert("RGBA")
    base = ImageEnhance.Brightness(base).enhance(0.3)
    draw = ImageDraw.Draw(base)
    y = 100
    for u in sorted(accs.keys()):
        draw.rounded_rectangle([40, y, 660, y+60], radius=15, fill=(50, 50, 50, 180))
        av = await get_roblox_avatar(u)
        if av: base.paste(av.resize((50,50)), (50, y+5), av.resize((50,50)))
        dur = format_duration(now - float(starts.get(u, now)))
        draw.text((120, y+15), f"{u} | {dur}", fill="white")
        y += 70
    return base

async def generate_session_img(user, sess_data, bg_url):
    base = await get_image_from_url(bg_url)
    base = base.resize((700, 500)).convert("RGBA")
    draw = ImageDraw.Draw(base)
    draw.text((50, 50), f"SESSIONS: {user}", fill="yellow")
    y = 100
    if not sess_data: draw.text((50, 150), "No data found", fill="white")
    for s in sess_data:
        date_str = time.strftime('%d.%m %H:%M', time.localtime(s[0]))
        draw.text((50, y), f"📅 {date_str} | Time: {format_duration(s[2])}", fill="white")
        y += 40
    return base

async def get_image_from_url(url):
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r: return Image.open(io.BytesIO(await r.read()))

# --- Запуск ---

async def main():
    await init_db()
    asyncio.create_task(update_loop())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
