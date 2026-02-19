import os
import asyncio
import time
import json
import redis.asyncio as redis
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiohttp import web

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
            print("✅ База данных загружена успешно")
        except Exception as e:
            print(f"❌ Ошибка БД: {e}")

async def save_to_db():
    if db:
        try:
            payload = {"notifs": notifications, "disabled": disabled_users, "global_disable": global_disable}
            await db.set("roblox_v5_data", json.dumps(payload))
        except: pass

# --- Команды ---

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    welcome_text = (
        "<b>🤖 Бот-мониторинг Roblox запущен!</b>\n\n"
        "<b>Доступные команды:</b>\n"
        "• /ping — создать таблицу и закрепить её\n"
        "• /add [Ник] — добавить себя в уведомления\n"
        "• /list — список всех настроек и статусов паузы\n"
        "• /disable — выключить уведомления для себя\n"
        "• /enable — включить уведомления обратно\n"
        "• /remove [Ник] — удалить свой пинг\n\n"
        "<i>Отправьте сигнал из Roblox, чтобы аккаунты появились в списке.</i>"
    )
    await message.answer(welcome_text, parse_mode="HTML")

@dp.message(Command("add"))
async def add_notify(message: types.Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if not args:
        return await message.answer("Использование: <code>/add Ник @user1 @user2</code>", parse_mode="HTML")
    
    rbx_name = args[0]
    mentions = args[1:] if len(args) > 1 else [get_user_id(message)]
    
    if rbx_name not in notifications:
        notifications[rbx_name] = []
    
    added = 0
    for m in mentions:
        if m not in notifications[rbx_name]:
            notifications[rbx_name].append(m)
            added += 1
            
    if added > 0:
        await save_to_db()
        await message.answer(f"✅ Добавлено ({added}) пингов для <code>{safe_html(rbx_name)}</code>", parse_mode="HTML")
    else:
        await message.answer("Эти пользователи уже есть в списке.")

@dp.message(Command("list"))
async def list_notifications(message: types.Message):
    if not notifications:
        return await message.answer("Список пингов пуст.")
    
    header = "<b>🔔 Настройки пингов:</b>"
    if global_disable: header += " ⚠️ (ГЛОБАЛЬНАЯ ПАУЗА)"
    
    text = f"{header}\n\n"
    empty_keys = []
    
    for rbx, users in notifications.items():
        if not users:
            empty_keys.append(rbx)
            continue
        
        formatted_users = []
        for u_mention in users:
            is_muted = False
            for d_uid, d_status in disabled_users.items():
                if d_uid in u_mention:
                    if d_status == "all" or rbx in d_status:
                        is_muted = True; break
            
            suffix = " 🔇" if is_muted else ""
            formatted_users.append(f"{u_mention}{suffix}")
            
        text += f"• <code>{safe_html(rbx)}</code>: {', '.join(formatted_users)}\n"
    
    # Чистим пустые записи
    for k in empty_keys: notifications.pop(k, None)
    
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("disable"))
async def disable_cmd(message: types.Message, command: CommandObject):
    global global_disable
    uid = get_user_id(message)
    arg = command.args.strip() if command.args else None
    
    if arg == "all":
        global_disable = True
        await message.answer("⚠️ <b>Глобальная пауза</b>: Уведомления выключены для ВСЕХ.")
    elif not arg:
        disabled_users[uid] = "all"
        await message.answer("🔇 Ваши уведомления отключены для всех аккаунтов.")
    else:
        if uid not in disabled_users or disabled_users[uid] == "all":
            disabled_users[uid] = []
        if arg not in disabled_users[uid]:
            disabled_users[uid].append(arg)
        await message.answer(f"🔇 Ваши уведомления для <code>{safe_html(arg)}</code> отключены.")
    await save_to_db()

@dp.message(Command("enable"))
async def enable_cmd(message: types.Message, command: CommandObject):
    global global_disable
    uid = get_user_id(message)
    arg = command.args.strip() if command.args else None

    if arg == "all":
        global_disable = False
        await message.answer("🔊 Глобальные пинги снова включены.")
    else:
        if uid in disabled_users:
            del disabled_users[uid]
            await message.answer("🔊 Ваши уведомления снова включены.")
        else:
            await message.answer("У вас и так всё включено.")
    await save_to_db()

@dp.message(Command("remove"))
async def remove_cmd(message: types.Message, command: CommandObject):
    uid = get_user_id(message)
    args = command.args.split() if command.args else []
    
    if not args:
        for rbx in notifications:
            notifications[rbx] = [m for m in notifications[rbx] if uid not in m]
        await message.answer("🗑 Вы удалены из всех списков.")
    else:
        rbx_name = args[0]
        target = args[1] if len(args) > 1 else uid
        if rbx_name in notifications:
            notifications[rbx_name] = [m for m in notifications[rbx_name] if target not in m]
            await message.answer(f"✅ Пинг {target} удален для {rbx_name}")
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
    msg = await bot.send_message(chat_id=str(status_chat_id), text="⏳ Сбор данных...")
    status_message_id = msg.message_id
    
    try:
        await bot.pin_chat_message(chat_id=str(status_chat_id), message_id=status_message_id, disable_notification=True)
        await bot.delete_message(chat_id=str(status_chat_id), message_id=status_message_id + 1)
    except: pass

# --- Циклы обновления ---
async def update_status_message():
    if not status_chat_id or not status_message_id: return
    now = time.time()
    
    if not accounts:
        text = "<b>📊 Мониторинг</b>\n⚠️ Ожидание сигналов..."
    else:
        p_label = " ❗(PAUSE)" if global_disable else ""
        text = f"<b>📊 Мониторинг Roblox</b>\n🕒 {time.strftime('%H:%M:%S')}{p_label}\n\n"
        for user in sorted(accounts.keys()):
            is_online = now - accounts[user] < 120
            
            if user in last_status and last_status[user] and not is_online:
                dur = format_duration(now - start_times.get(user, now))
                if user in notifications and not global_disable:
                    active_pings = []
                    for m in notifications[user]:
                        muted = False
                        for d_uid, d_st in disabled_users.items():
                            if d_uid in m and (d_st == "all" or user in d_st):
                                muted = True; break
                        if not muted: active_pings.append(m)
                    
                    if active_pings:
                        try:
                            await bot.send_message(
                                str(status_chat_id), 
                                f"⚠️ <b>{safe_html(user)}</b> ВЫЛЕТЕЛ!\n⏱ Был в сети: {dur}\n{' '.join(active_pings)}", 
                                parse_mode="HTML"
                            )
                        except: pass
                start_times.pop(user, None)

            last_status[user] = is_online
            if is_online:
                if user not in start_times: start_times[user] = now
                text += f"🟢 <code>{safe_html(user)}</code> | ⏱ {format_duration(now - start_times[user])}\n"
            else:
                text += f"🔴 <code>{safe_html(user)}</code>\n"
    
    try:
        await bot.edit_message_text(text=text, chat_id=str(status_chat_id), message_id=status_message
