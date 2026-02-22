import os
import asyncio
import time
import json
import random
import logging
import redis.asyncio as redis
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiohttp import web

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("BSS_STABLE")

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))
ALLOWED_ADMIN = "Gold_mod1"

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Данные
accounts, start_times, notifications, status_messages, last_text = {}, {}, {}, {}, {}
total_restarts, session_restarts = 0, 0

QUOTES = [
    "🐝 Пчёлы не спят, они фармят!",
    "🍯 Мёд сам себя не соберёт.",
    "🚀 Удачного фарма, легенда!",
    "⭐ Твой Улей — твои правила.",
    "🛡️ Мониторинг на страже профита."
]

class PostCreation(StatesGroup):
    waiting_for_content = State()
    waiting_for_title = State()
    waiting_for_desc = State()
    waiting_for_confirm = State()

# --- База Данных ---
async def load_data():
    global db, notifications, status_messages, total_restarts, session_restarts, start_times, accounts
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V37_STABLE_FINAL")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            total_restarts = data.get("total_restarts", 0) + 1
            session_restarts = data.get("session_restarts", 0) + 1
            
            saved_starts = data.get("starts", {})
            saved_accounts = data.get("accounts", {})
            now = time.time()
            for u, l_ping in saved_accounts.items():
                if now - float(l_ping) < 120: # Если бот рестартнулся быстро
                    accounts[u] = float(l_ping)
                    if u in saved_starts: start_times[u] = float(saved_starts[u])
            logger.info(f"✅ Данные восстановлены. Рестарт сессии: {session_restarts}")
    except Exception as e:
        logger.error(f"Ошибка БД: {e}")

async def save_data():
    if not db: return
    try:
        data = {
            "notifs": notifications, "msgs": status_messages,
            "total_restarts": total_restarts, "session_restarts": session_restarts,
            "starts": start_times, "accounts": accounts
        }
        await db.set("BSS_V37_STABLE_FINAL", json.dumps(data))
    except: pass

# --- Логика Панели ---
def get_status_text():
    now = time.time()
    text = f"<b>🐝 Состояние Улья BSS</b>\n"
    text += f"🕒 {time.strftime('%H:%M:%S')} | 🔄 Рестартов: {session_restarts}\n\n"
    text += "<blockquote>"
    if not accounts:
        text += "<i>Ожидание сигналов...</i>"
    else:
        for u in sorted(accounts.keys()):
            s_time = start_times.get(u, now)
            dur = int(now - s_time)
            res = f"{dur//3600}ч {(dur%3600)//60}м {dur%60}с"
            text += f"🟢 <code>{u}</code> | <b>{res}</b>\n"
    text += "</blockquote>\n"
    text += f"<i>{random.choice(QUOTES)}</i>"
    return text

async def refresh_panels():
    text = get_status_text()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить сессию", callback_data="ask_reset")]])
    for cid, mid in list(status_messages.items()):
        if last_text.get(str(cid)) == text: continue
        try:
            await bot.edit_message_text(chat_id=str(cid), message_id=int(mid), text=text, parse_mode="HTML", reply_markup=kb)
            last_text[str(cid)] = text
        except Exception as e:
            if "not modified" not in str(e).lower(): logger.error(f"Ошибка обновления {cid}: {e}")

# --- Команды ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer(
        "<b>🐝 Улей v51 Stable</b>\n\n"
        "/information - Панель\n"
        "/add [Ник] [Тег] - Пинг\n"
        "/list - Твои пинги\n"
        "/Update - Рассылка (Админ)\n"
        "/testdisconect [Ник] - Тест", parse_mode="HTML"
    )

@dp.message(Command("information"))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(chat_id=cid, message_id=status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить сессию", callback_data="ask_reset")]]))
    status_messages[cid] = msg.message_id
    try: await bot.pin_chat_message(chat_id=cid, message_id=msg.message_id, disable_notification=True)
    except: pass
    await save_data()

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Укажи ник! Можно так: <code>/add Player1 @ivan</code>", parse_mode="HTML")
    acc = args[1]
    tag = args[2] if len(args) > 2 else (f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}")
    notifications.setdefault(acc, [])
    if tag not in notifications[acc]: notifications[acc].append(tag)
    await save_data()
    await m.answer(f"✅ Пинг для <b>{acc}</b> на <b>{tag}</b> добавлен", parse_mode="HTML")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Список пингов пуст.")
    res = "<b>📜 Настройки пингов:</b>\n"
    for acc, tags in notifications.items():
        res += f"• <code>{acc}</code>: {', '.join(tags)}\n"
    await m.answer(res, parse_mode="HTML")

@dp.message(Command("testdisconect"))
async def cmd_test(m: types.Message):
    if m.from_user.username != ALLOWED_ADMIN: return
    args = m.text.split()
    if len(args) > 1 and args[1] in accounts:
        accounts[args[1]] = time.time() - 200
        await m.answer(f"🧪 Тест вылета <code>{args[1]}</code> запущен...", parse_mode="HTML")

# --- Рассылка /Update ---
@dp.message(Command("Update"))
async def cmd_update(m: types.Message, state: FSMContext):
    if m.from_user.username != ALLOWED_ADMIN: return
    await state.set_data({"photos": []})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📝 С названием", callback_data="u_t"), InlineKeyboardButton(text="📄 Без", callback_data="u_s")]])
    await m.answer("Тип рассылки:", reply_markup=kb)

@dp.callback_query(F.data.startswith("u_"))
async def u_choice(cb: types.CallbackQuery, state: FSMContext):
    if cb.data == "u_t":
        await state.set_state(PostCreation.waiting_for_title); await cb.message.answer("Заголовок:")
    else:
        await state.set_state(PostCreation.waiting_for_content); await cb.message.answer("Текст:")
    await cb.answer()

@dp.message(PostCreation.waiting_for_title, F.text | F.photo)
@dp.message(PostCreation.waiting_for_content, F.text | F.photo)
@dp.message(PostCreation.waiting_for_desc, F.text | F.photo)
async def collect(m: types.Message, state: FSMContext):
    d = await state.get_data(); photos = d.get("photos", [])
    if m.photo: photos.append(m.photo[-1].file_id); await state.update_data(photos=photos)
    txt = m.html_text or m.caption
    st = await state.get_state()
    if st == PostCreation.waiting_for_title:
        await state.update_data(title=txt.upper()); await state.set_state(PostCreation.waiting_for_desc); await m.answer("Описание:")
    else:
        d = await state.get_data(); final = f"📢 <b>{d.get('title')}</b>\n\n{txt}" if d.get('title') else f"📢 {txt}"
        await state.update_data(full_text=final)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ ОТПРАВИТЬ", callback_data="go"), InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="no")]])
        if photos:
            if len(photos) == 1: await m.answer_photo(photos[0], caption=final, parse_mode="HTML", reply_markup=kb)
            else:
                media = [InputMediaPhoto(media=photos[0], caption=final, parse_mode="HTML")] + [InputMediaPhoto(media=p) for p in photos[1:]]
                await m.answer_media_group(media); await m.answer("Отправить альбом?", reply_markup=kb)
        else: await m.answer(final, parse_mode="HTML", reply_markup=kb)
        await state.set_state(PostCreation.waiting_for_confirm)

@dp.callback_query(F.data == "go", PostCreation.waiting_for_confirm)
async def go_send(cb: types.CallbackQuery, state: FSMContext):
    d = await state.get_data(); text, photos = d['full_text'], d.get("photos", [])
    for cid in status_messages:
        try:
            if not photos: await bot.send_message(cid, text, parse_mode="HTML")
            elif len(photos) == 1: await bot.send_photo(cid, photos[0], caption=text, parse_mode="HTML")
            else: await bot.send_media_group(cid, [InputMediaPhoto(media=p, caption=text if i==0 else "", parse_mode="HTML") for i, p in enumerate(photos)])
        except: pass
    await cb.message.answer("🚀 Разослано!"); await state.clear(); await cb.answer()

# --- Сигналы и Монитор ---
@dp.callback_query(F.data == "ask_reset")
async def ask_res(cb: types.CallbackQuery):
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚠️ ПОДТВЕРДИТЬ", callback_data="confirm_reset")]]))

@dp.callback_query(F.data == "confirm_reset")
async def conf_res(cb: types.CallbackQuery):
    global session_restarts; session_restarts = 0; await save_data(); await cb.answer("Сессия сброшена!"); await refresh_panels()

async def handle_signal(request):
    try:
        data = await request.json(); u = data.get("username")
        if u:
            now = time.time()
            if u not in start_times: start_times[u] = now
            accounts[u] = now
            asyncio.create_task(refresh_panels()); return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def monitor():
    while True:
        now = time.time()
        for u in list(accounts.keys()):
            if now - accounts[u] > 120:
                if u in notifications:
                    tags = " ".join(notifications[u])
                    for cid in status_messages:
                        try: await bot.send_message(cid, f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{tags}", parse_mode="HTML")
                        except: pass
                accounts.pop(u, None); start_times.pop(u, None)
        await refresh_panels(); await save_data(); await asyncio.sleep(30)

async def main():
    await load_data(); asyncio.create_task(monitor())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
