import os
import asyncio
import time
import json
import redis.asyncio as redis
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiohttp import web

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))
ALLOWED_ADMIN = "Gold_mod1"

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Данные в памяти
accounts = {}      
start_times = {}   
notifications = {} 
status_messages = {}
total_restarts = 0     
session_restarts = 0   
last_text = {} 

def logger(msg):
    print(f"DEBUG [{time.strftime('%H:%M:%S')}]: {msg}")

# --- Состояния для рассылки ---
class PostCreation(StatesGroup):
    waiting_for_title = State()
    waiting_for_desc = State()
    waiting_for_simple_text = State()
    waiting_for_confirm = State()

# --- База Данных (СТРОГО БЕЗ ИЗМЕНЕНИЙ КЛЮЧА) ---
async def load_data():
    global db, notifications, status_messages, total_restarts, session_restarts, start_times
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V37_STABLE_FINAL")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            total_restarts = data.get("restarts", 0) + 1
            session_restarts = data.get("session_restarts", 0) + 1
            saved_starts = data.get("starts", {})
            for k, v in saved_starts.items(): start_times[k] = float(v)
            logger(f"✅ База подключена. Общих рестартов: {total_restarts}")
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

# --- Клавиатуры Панели ---
def get_panel_kb(confirm=False):
    if not confirm:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Сбросить рестарты", callback_data="ask_reset")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ ТЫ УВЕРЕН? (ЖМИ)", callback_data="confirm_reset")]
    ])

# --- Логика Текста Панели ---
def get_status_text():
    now = time.time()
    text = f"<b>🐝 Состояние Улья BSS</b>\n"
    text += f"🕒 {time.strftime('%H:%M:%S')} | 🔄 Рестартов: {session_restarts}\n\n"
    if not accounts:
        text += "<i>Ожидание сигналов от макросов...</i>"
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
                text=text, parse_mode="HTML", reply_markup=get_panel_kb()
            )
            last_text[str(cid)] = text
        except: pass

# --- Обработка Кнопки Сброса ---
@dp.callback_query(F.data == "ask_reset")
async def ask_reset(callback: types.CallbackQuery):
    try:
        await callback.message.edit_reply_markup(reply_markup=get_panel_kb(confirm=True))
        await asyncio.sleep(5)
        await callback.message.edit_reply_markup(reply_markup=get_panel_kb(confirm=False))
    except: pass

@dp.callback_query(F.data == "confirm_reset")
async def confirm_reset(callback: types.CallbackQuery):
    global session_restarts
    session_restarts = 0
    user = callback.from_user.username or callback.from_user.id
    logger(f"⚠️ {user} сбросил сессионные рестарты.")
    await save_data()
    await callback.answer("Сброшено!")
    await refresh_panels()

# --- СИСТЕМА РАССЫЛКИ /Update ---
@dp.message(Command("Update"))
async def cmd_update(m: types.Message, state: FSMContext):
    if m.from_user.username != ALLOWED_ADMIN: return
    logger(f"📢 {m.from_user.username} начал создание поста.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 С названием", callback_data="type_with_title")],
        [InlineKeyboardButton(text="📄 Без названия", callback_data="type_no_title")]
    ])
    await m.answer("Тип сообщения:", reply_markup=kb)

@dp.callback_query(F.data.startswith("type_"))
async def choose_type(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "type_with_title":
        await state.set_state(PostCreation.waiting_for_title)
        await callback.message.answer("Пришли заголовок (можно с фото):")
    else:
        await state.set_state(PostCreation.waiting_for_simple_text)
        await callback.message.answer("Пришли текст поста (можно с фото):")
    await callback.answer()

async def get_content(m: types.Message, state: FSMContext):
    if m.photo:
        await state.update_data(photo=m.photo[-1].file_id)
        return m.caption or m.text
    return m.text

@dp.message(PostCreation.waiting_for_title)
async def stage_title(m: types.Message, state: FSMContext):
    txt = await get_content(m, state)
    if not txt: return await m.answer("Нужен текст!")
    await state.update_data(title=txt.upper())
    await state.set_state(PostCreation.waiting_for_desc)
    await m.answer("Теперь пришли описание:")

@dp.message(PostCreation.waiting_for_desc)
async def stage_desc(m: types.Message, state: FSMContext):
    desc = await get_content(m, state)
    data = await state.get_data()
    full = f"📢 <b>{data['title']}</b>\n\n{desc}"
    await state.update_data(full_text=full)
    await send_preview(m, state, full)

@dp.message(PostCreation.waiting_for_simple_text)
async def stage_simple(m: types.Message, state: FSMContext):
    txt = await get_content(m, state)
    full = f"📢 <b>Объявление:</b>\n\n{txt}"
    await state.update_data(full_text=full)
    await send_preview(m, state, full)

async def send_preview(m: types.Message, state: FSMContext, text: str):
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ ОТПРАВИТЬ", callback_data="broadcast_confirm")],
        [InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="broadcast_cancel")]
    ])
    await m.answer("<b>ПРЕВЬЮ:</b>", parse_mode="HTML")
    if data.get("photo"):
        await m.answer_photo(photo=data["photo"], caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        await m.answer(text, parse_mode="HTML", reply_markup=kb)
    await state.set_state(PostCreation.waiting_for_confirm)

@dp.callback_query(F.data == "broadcast_confirm", PostCreation.waiting_for_confirm)
async def broadcast_final(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sent = 0
    for cid in status_messages.keys():
        try:
            if data.get("photo"):
                await bot.send_photo(cid, photo=data["photo"], caption=data["full_text"], parse_mode="HTML")
            else:
                await bot.send_message(cid, text=data["full_text"], parse_mode="HTML")
            sent += 1
        except: pass
    logger(f"🚀 Рассылка завершена. Чатов: {sent}")
    await callback.message.answer(f"Отправлено в {sent} чатов.")
    await state.clear(); await callback.answer()

@dp.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear(); await callback.message.answer("Отменено."); await callback.answer()

# --- Обычные Команды ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer(f"📊 Общих рестартов: {total_restarts}\n/information - Панель")

@dp.message(Command("information"))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(cid, status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=get_panel_kb())
    status_messages[cid] = msg.message_id
    try:
        await bot.pin_chat_message(cid, msg.message_id, disable_notification=True)
        await asyncio.sleep(1); await bot.delete_message(cid, msg.message_id + 1)
    except: pass
    await save_data()

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return
    acc = args[1]; tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    notifications.setdefault(acc, []).append(tag)
    await save_data(); await m.answer(f"✅ Пинг {acc} включен")

@dp.message(Command("remove", "delete"))
async def cmd_remove(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return
    acc = args[1]; tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    if acc in notifications and tag in notifications[acc]:
        notifications[acc].remove(tag)
        if not notifications[acc]: del notifications[acc]
        await save_data(); await m.answer(f"❌ Пинг {acc} удален")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Список пуст")
    res = "<b>📜 Твои подписки:</b>\n"
    for k, v in notifications.items(): res += f"• <code>{k}</code>: {', '.join(set(v))}\n"
    await m.answer(res, parse_mode="HTML")

# --- Потоки и Сигналы ---
async def handle_signal(request):
    try:
        data = await request.json()
        u = data.get("username")
        if u:
            if u not in start_times: start_times[u] = time.time()
            accounts[u] = time.time()
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
        await refresh_panels(); await save_data(); await asyncio.sleep(30)

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
