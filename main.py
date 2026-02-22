import os
import asyncio
import time
import json
import redis.asyncio as redis
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

# --- Конфигурация ---
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
total_restarts = 0     
session_restarts = 0   
last_text = {} 

def logger(msg):
    print(f"DEBUG [{time.strftime('%H:%M:%S')}]: {msg}")

# --- Клавиатуры ---
def get_kb(confirm=False):
    if not confirm:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Сбросить рестарты", callback_data="ask_reset")]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚠️ ТЫ УВЕРЕН? (ЖМИ)", callback_data="confirm_reset")]
        ])

# --- База Данных ---
async def load_data():
    global db, notifications, status_messages, total_restarts, session_restarts, start_times
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V37_STABLE_FINAL") # КЛЮЧ НЕ МЕНЯЕМ
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            total_restarts = data.get("restarts", 0) + 1
            session_restarts = data.get("session_restarts", 0) + 1
            saved_starts = data.get("starts", {})
            for k, v in saved_starts.items(): start_times[k] = float(v)
            logger(f"✅ Данные загружены. Рестарт системы.")
    except Exception as e:
        logger(f"Ошибка БД: {e}")

async def save_data():
    if not db: return
    try:
        data = {
            "notifs": notifications, 
            "msgs": status_messages, 
            "restarts": total_restarts,               
            "session_restarts": session_restarts,     
            "starts": start_times 
        }
        await db.set("BSS_V37_STABLE_FINAL", json.dumps(data))
    except: pass

# --- Логика текста ---
def get_status_text():
    now = time.time()
    text = f"<b>🐝 Состояние макро BSS</b>\n"
    text += f"🕒 {time.strftime('%H:%M:%S')} | 🔄 Рестартов: {session_restarts}\n\n"
    if not accounts:
        text += "<i>Ожидание сигналов...</i>"
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
            await bot.edit_message_text(
                chat_id=str(cid), message_id=int(mid),
                text=text, parse_mode="HTML", reply_markup=get_kb()
            )
            last_text[str(cid)] = text
        except: pass

# --- Обработка Кнопок ---

@dp.callback_query(F.data == "ask_reset")
async def ask_reset(callback: types.CallbackQuery):
    # Меняем кнопку на подтверждение
    try:
        await callback.message.edit_reply_markup(reply_markup=get_kb(confirm=True))
        # Через 5 секунд вернем кнопку назад, если не нажали
        await asyncio.sleep(5)
        await callback.message.edit_reply_markup(reply_markup=get_kb(confirm=False))
    except: pass

@dp.callback_query(F.data == "confirm_reset")
async def confirm_reset(callback: types.CallbackQuery):
    global session_restarts
    session_restarts = 0
    user_info = f"@{callback.from_user.username}" if callback.from_user.username else f"ID:{callback.from_user.id}"
    logger(f"⚠️ {user_info} ПОДТВЕРДИЛ сброс рестартов.")
    await save_data()
    await callback.answer("✅ Сессионные рестарты сброшены!", show_alert=False)
    await refresh_panels()

# --- Команды ---

@dp.message(Command("start", ignore_case=True))
async def cmd_start(m: types.Message):
    res = f"<b>🐝 Бот Улья</b>\n\n/information - панель\n/add - пинг\n/remove - удалить\n\n📊 <b>Общих рестартов:</b> {total_restarts}"
    await m.answer(res, parse_mode="HTML")

@dp.message(Command("information", ignore_case=True))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(chat_id=cid, message_id=status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=get_kb())
    status_messages[cid] = msg.message_id
    try:
        await bot.pin_chat_message(chat_id=cid, message_id=msg.message_id, disable_notification=True)
        await asyncio.sleep(1); await bot.delete_message(chat_id=cid, message_id=msg.message_id + 1)
    except: pass
    await save_data()

@dp.message(Command("add", ignore_case=True))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Ник?")
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    notifications.setdefault(acc, []).append(tag)
    await save_data(); await m.answer(f"✅ Пинг {acc} добавлен")

@dp.message(Command("delete", "remove", ignore_case=True))
async def cmd_remove(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    if acc in notifications and tag in notifications[acc]:
        notifications[acc].remove(tag)
        if not notifications[acc]: del notifications[acc]
        await save_data(); await m.answer(f"❌ Пинг {acc} удален")

@dp.message(Command("list", ignore_case=True))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Пусто")
    res = "<b>📜 Твои подписки:</b>\n"
    for k, v in notifications.items(): res += f"• <code>{k}</code>: {', '.join(set(v))}\n"
    await m.answer(res, parse_mode="HTML")

# --- Потоки ---

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
            if now - accounts[u] > 120:
                if u in notifications:
                    for cid in status_messages:
                        try: await bot.send_message(str(cid), f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{' '.join(notifications[u])}", parse_mode="HTML")
                        except: pass
                accounts.pop(u); start_times.pop(u, None)
        await refresh_panels(); await save_data()
        await asyncio.sleep(30)

async def main():
    await load_data()
    asyncio.create_task(monitor())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await asyncio.sleep(5); await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
