import os
import asyncio
import time
import json
import io
import redis.asyncio as redis
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# --- Глобальные переменные ---
accounts = {}       
start_times = {}    
last_status = {}    
notifications = {}  
disabled_users = {} 
global_disable = False

status_chat_id = None
status_message_id = None

# --- Утилиты ---
def safe_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def format_duration(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    if h > 0: return f"{h}ч {m}м {s}с"
    if m > 0: return f"{m}м {s}с"
    return f"{s}с"

def get_user_id(message: types.Message):
    u = message.from_user
    return f"@{u.username}" if u.username else f"ID:{u.id}"

# --- Работа с БД ---
async def init_db():
    global db, notifications, disabled_users, global_disable
    if REDIS_URL:
        try:
            db = redis.from_url(REDIS_URL, decode_responses=True)
            data = await db.get("roblox_v5_data")
            if data:
                saved = json.loads(data)
                notifications.update(saved.get("notifs", {}))
                disabled_users.update(saved.get("disabled", {}))
                global_disable = saved.get("global_disable", False)
            print("✅ База данных загружена")
        except Exception as e:
            print(f"❌ Ошибка БД: {e}")

async def save_to_db():
    if db:
        try:
            payload = {"notifs": notifications, "disabled": disabled_users, "global_disable": global_disable}
            await db.set("roblox_v5_data", json.dumps(payload))
        except: pass

# --- Обработчики команд ---

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    guide = (
        "<b>🚀 Инструкция по управлению мониторингом</b>\n\n"
        "<b>1. Настройка уведомлений:</b>\n"
        "• <code>/add Ник</code> — подписаться на уведомления о вылете аккаунта.\n"
        "• <code>/add Ник @user</code> — подписать другого человека.\n"
        "• <code>/remove Ник</code> — перестать получать уведомления для этого ника.\n\n"
        "<b>2. Управление таблицей:</b>\n"
        "• <code>/ping</code> — создать новую таблицу. Старая таблица удалится, а новая закрепится в чате автоматически.\n"
        "• <code>/img_create</code> — сгенерировать картинку с текущим состоянием всех аккаунтов.\n\n"
        "<b>3. Режим паузы (Мут):</b>\n"
        "• <code>/disable</code> — выключить все уведомления для ВАС (в списке появится 🔇).\n"
        "• <code>/disable Ник</code> — выключить уведомления только для конкретного аккаунта.\n"
        "• <code>/disable all</code> — поставить весь бот на паузу (уведомления не придут никому).\n"
        "• <code>/enable</code> — включить ваши уведомления обратно.\n\n"
        "<b>4. Просмотр данных:</b>\n"
        "• <code>/list</code> — посмотреть, кто на какие аккаунты подписан и у кого выключен звук."
    )
    await message.answer(guide, parse_mode="HTML")

@dp.message(Command("list"))
async def list_notifications(message: types.Message):
    if not notifications: return await message.answer("Список пуст.")
    header = "<b>🔔 Настройки пингов:</b>"
    if global_disable: header += " ⚠️ (ГЛОБАЛЬНАЯ ПАУЗА)"
    text = f"{header}\n\n"
    for rbx, users in notifications.items():
        if not users: continue
        fmt_users = []
        for u in users:
            is_muted = False
            u_clean = u.lower().strip()
            for d_uid, d_st in disabled_users.items():
                if d_uid.lower() in u_clean:
                    if d_st == "all" or rbx in d_st:
                        is_muted = True; break
            fmt_users.append(f"{u}{' 🔇' if is_muted else ''}")
        text += f"• <code>{safe_html(rbx)}</code>: {', '.join(fmt_users)}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("Img_Create"))
async def create_image_status(message: types.Message):
    if not accounts: return await message.answer("Нет данных.")
    width, height = 600, 100 + (len(accounts) * 40)
    img = Image.new('RGB', (width, height), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), f"Roblox Status - {time.strftime('%H:%M:%S')}", fill=(255, 255, 255))
    draw.line((20, 50, 580, 50), fill=(100, 100, 100))
    y, now = 70, time.time()
    for user in sorted(accounts.keys()):
        is_online = now - accounts[user] < 120
        color = (0, 255, 0) if is_online else (255, 0, 0)
        draw.ellipse((20, y, 35, y+15), fill=color)
        session = f"({format_duration(now - start_times[user])})" if is_online and user in start_times else ""
        draw.text((50, y), f"{user} {session}", fill=(255, 255, 255))
        draw.text((450, y), "ONLINE" if is_online else "OFFLINE", fill=color)
        y += 40
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    await message.answer_photo(BufferedInputFile(buf.read(), filename="status.png"), caption="📊 Текущий отчет")

@dp.message(Command("disable"))
async def disable_cmd(message: types.Message, command: CommandObject):
    global global_disable
    uid = get_user_id(message)
    arg = command.args.strip() if command.args else None
    if arg == "all":
        global_disable = True
        await message.answer("⚠️ Пинги отключены для ВСЕХ.")
    elif not arg:
        disabled_users[uid] = "all"
        await message.answer("🔇 Ваши пинги выключены везде.")
    else:
        if uid not in disabled_users or disabled_users[uid] == "all": disabled_users[uid] = []
        if arg not in disabled_users[uid]: disabled_users[uid].append(arg)
        await message.answer(f"🔇 Пинги для <code>{safe_html(arg)}</code> выключены.")
    await save_to_db()

@dp.message(Command("enable"))
async def enable_cmd(message: types.Message, command: CommandObject):
    global global_disable
    uid = get_user_id(message)
    arg = command.args.strip() if command.args else None
    if arg == "all":
        global_disable = False
        await message.answer("🔊 Глобальная пауза снята.")
    else:
        disabled_users.pop(uid, None)
        await message.answer("🔊 Ваши пинги снова включены.")
    await save_to_db()

@dp.message(Command("add"))
async def add_notify(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args: return await message.answer("Использование: /add Ник")
    rbx_name = args[0]
    mentions = args[1:] if len(args) > 1 else [get_user_id(message)]
    if rbx_name not in notifications: notifications[rbx_name] = []
    for m in mentions:
        if m not in notifications[rbx_name]: notifications[rbx_name].append(m)
    await save_to_db()
    await message.answer(f"✅ Список пингов обновлен для <code>{safe_html(rbx_name)}</code>", parse_mode="HTML")

@dp.message(Command("remove"))
async def remove_cmd(message: types.Message, command: CommandObject):
    uid = get_user_id(message).lower()
    args = command.args.split() if command.args else []
    if not args:
        for rbx in notifications:
            notifications[rbx] = [m for m in notifications[rbx] if uid not in m.lower()]
        await message.answer("🗑 Вы удалены из всех списков.")
    else:
        rbx_name = args[0]
        if rbx_name in notifications:
            notifications[rbx_name] = [m for m in notifications[rbx_name] if uid not in m.lower()]
            await message.answer(f"✅ Пинг удален для {rbx_name}")
    await save_to_db()

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    global status_chat_id, status_message_id
    try: await message.delete()
    except: pass
    try:
        chat = await bot.get_chat(message.chat.id)
        if chat.pinned_message and chat.pinned_message.from_user.id == bot.id:
            await bot.delete_message(message.chat.id, chat.pinned_message.message_id)
    except: pass
    status_chat_id = message.chat.id
    msg = await bot.send_message(chat_id=str(status_chat_id), text="⏳ Запуск...")
    status_message_id = msg.message_id
    try:
        await bot.pin_chat_message(chat_id=str(status_chat_id), message_id=status_message_id, disable_notification=True)
        await bot.delete_message(chat_id=str(status_chat_id), message_id=status_message_id + 1)
    except: pass

async def update_status_message():
    if not status_chat_id or not status_message_id: return
    now = time.time()
    if not accounts:
        text = "<b>📊 Мониторинг</b>\n⚠️ Ждем сигналов..."
    else:
        p_label = " ❗(PAUSE)" if global_disable else ""
        text = f"<b>📊 Мониторинг Roblox</b>\n🕒 {time.strftime('%H:%M:%S')}{p_label}\n\n"
        for user in sorted(accounts.keys()):
            is_online = now - accounts[user] < 120
            if user in last_status and last_status[user] and not is_online:
                dur = format_duration(now - start_times.get(user, now))
                if user in notifications and not global_disable:
                    active = []
                    for m in notifications[user]:
                        muted, m_low = False, m.lower()
                        for d_uid, d_st in disabled_users.items():
                            if d_uid.lower() in m_low and (d_st == "all" or user in d_st):
                                muted = True; break
                        if not muted: active.append(m)
                    if active:
                        try: await bot.send_message(str(status_chat_id), f"⚠️ <b>{safe_html(user)}</b> ВЫЛЕТЕЛ!\n⏱ Был в сети: {dur}\n{' '.join(active)}", parse_mode="HTML")
                        except: pass
                start_times.pop(user, None)
            last_status[user] = is_online
            if is_online:
                if user not in start_times: start_times[user] = now
                text += f"🟢 <code>{safe_html(user)}</code> | ⏱ {format_duration(now - start_times[user])}\n"
            else: text += f"🔴 <code>{safe_html(user)}</code>\n"
    try: await bot.edit_message_text(text=text, chat_id=str(status_chat_id), message_id=status_message_id, parse_mode="HTML")
    except: pass

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            user = data["username"]
            accounts[user], last_status[user] = time.time(), True
            if user not in start_times: start_times[user] = time.time()
            return web.Response(text="OK")
    except: pass
    return web.Response(text="Error", status=400)

async def main():
    await init_db()
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    asyncio.create_task(status_updater())
    await dp.start_polling(bot)

async def status_updater():
    while True:
        await update_status_message(); await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
