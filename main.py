import os
import asyncio
import time
import json
import redis.asyncio as redis
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
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

accounts, start_times, notifications, status_messages, last_text = {}, {}, {}, {}, {}
total_restarts, session_restarts = 0, 0

def logger(msg):
    print(f"DEBUG [{time.strftime('%H:%M:%S')}]: {msg}")

class PostCreation(StatesGroup):
    waiting_for_content = State()
    waiting_for_title = State()
    waiting_for_desc = State()
    waiting_for_confirm = State()

# --- База Данных (Ключ без изменений) ---
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
            logger(f"✅ База загружена. Рестартов: {total_restarts}")
    except Exception as e: logger(f"Ошибка БД: {e}")

async def save_data():
    if not db: return
    try:
        data = {"notifs": notifications, "msgs": status_messages, "restarts": total_restarts, 
                "session_restarts": session_restarts, "starts": start_times}
        await db.set("BSS_V37_STABLE_FINAL", json.dumps(data))
    except: pass

# --- Тестовые команды ---
@dp.message(Command("testadd"))
async def cmd_testadd(m: types.Message):
    if m.from_user.username != ALLOWED_ADMIN: return
    u = m.text.split()[1] if len(m.text.split()) > 1 else None
    if u:
        now = time.time()
        if u not in start_times: start_times[u] = now
        accounts[u] = now
        await m.answer(f"🧪 Тест: {u} в сети.")
        await refresh_panels()

@dp.message(Command("testdisconect"))
async def cmd_testdis(m: types.Message):
    if m.from_user.username != ALLOWED_ADMIN: return
    u = m.text.split()[1] if len(m.text.split()) > 1 else None
    if u and u in accounts:
        accounts[u] = time.time() - 150 # Имитируем простой > 120 сек
        await m.answer(f"🧪 Тест: Дисконнект {u} имитирован. Ждем проверку монитора.")
    else:
        await m.answer("Аккаунт не найден в активных.")

# --- Логика Панели ---
def get_status_text():
    now = time.time()
    res = f"<b>🐝 Статус Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')} | 🔄 Рестартов: {session_restarts}\n\n"
    
    # ДОБАВЛЕНА ЦИТАТА В ПАНЕЛЬ
    res += "<blockquote>"
    if not accounts: 
        res += "Аккаунты офлайн..."
    else:
        for u in sorted(accounts.keys()):
            dur = int(now - start_times.get(u, now))
            res += f"🟢 <code>{u}</code> | <b>{dur//3600}ч {(dur%3600)//60}м</b>\n"
    res += "</blockquote>"
    return res

async def refresh_panels():
    text = get_status_text()
    for cid, mid in list(status_messages.items()):
        if last_text.get(str(cid)) == text: continue
        try:
            await bot.edit_message_text(
                chat_id=str(cid), 
                message_id=int(mid), 
                text=text, 
                parse_mode="HTML", 
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить рестарты за сессию", callback_data="ask_reset")]])
            )
            last_text[str(cid)] = text
        except Exception as e:
            if "not modified" not in str(e).lower(): logger(f"Update error: {e}")

# --- Мониторинг ---
async def monitor():
    while True:
        now = time.time()
        for u in list(accounts.keys()):
            if now - accounts[u] > 120:
                if u in notifications:
                    tags = ' '.join(notifications[u])
                    msg_text = f"🚨 <b>ВЫЛЕТ АККАУНТА</b>\n\n<blockquote>👤 <code>{u}</code>\n🔔 {tags}</blockquote>"
                    for cid in status_messages:
                        try: await bot.send_message(cid, msg_text, parse_mode="HTML")
                        except: pass
                accounts.pop(u, None); start_times.pop(u, None)
        await refresh_panels(); await save_data(); await asyncio.sleep(30)

# --- Рассылка /Update ---
@dp.message(Command("Update"))
async def cmd_update(m: types.Message, state: FSMContext):
    if m.from_user.username != ALLOWED_ADMIN: return
    await state.set_data({"photos": []})
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 С названием", callback_data="upd_with_title")],
        [InlineKeyboardButton(text="📄 Просто сообщение", callback_data="upd_simple")]
    ])
    await m.answer("<b>Режим создания поста</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("upd_"))
async def choose_upd_type(cb: types.CallbackQuery, state: FSMContext):
    if cb.data == "upd_with_title":
        await state.set_state(PostCreation.waiting_for_title)
        await cb.message.answer("Пришли <b>ЗАГОЛОВОК</b> (можно с фото):", parse_mode="HTML")
    else:
        await state.set_state(PostCreation.waiting_for_content)
        await cb.message.answer("Пришли <b>ТЕКСТ</b> (можно фото):", parse_mode="HTML")
    await cb.answer()

@dp.message(PostCreation.waiting_for_title, F.photo)
@dp.message(PostCreation.waiting_for_title, F.text)
@dp.message(PostCreation.waiting_for_content, F.photo)
@dp.message(PostCreation.waiting_for_content, F.text)
async def collect_content(m: types.Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    if m.photo:
        photos.append(m.photo[-1].file_id)
        await state.update_data(photos=photos)
    text = m.html_text or m.caption
    curr = await state.get_state()
    if curr == PostCreation.waiting_for_title:
        await state.update_data(title=text.upper() if text else "БЕЗ НАЗВАНИЯ")
        await state.set_state(PostCreation.waiting_for_desc)
        await m.answer("Введите <b>ОПИСАНИЕ</b>:", parse_mode="HTML")
    else:
        data = await state.get_data()
        final = f"📢 <b>{data.get('title')}</b>\n\n{text}" if data.get('title') else f"📢 {text}"
        await state.update_data(full_text=final)
        await show_preview(m, state)

@dp.message(PostCreation.waiting_for_desc)
async def get_description(m: types.Message, state: FSMContext):
    desc = m.html_text or m.caption
    data = await state.get_data()
    final = f"📢 <b>{data['title']}</b>\n\n{desc}"
    await state.update_data(full_text=final)
    await show_preview(m, state)

async def show_preview(m: types.Message, state: FSMContext):
    data = await state.get_data()
    text, photos = data.get("full_text", ""), data.get("photos", [])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ ОТПРАВИТЬ", callback_data="send_post"), InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="cancel_post")]])
    await m.answer("<b>ПРЕВЬЮ:</b>", parse_mode="HTML")
    if not photos: await m.answer(text, parse_mode="HTML", reply_markup=kb)
    elif len(photos) == 1: await m.answer_photo(photos[0], caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        media = [InputMediaPhoto(media=photos[0], caption=text, parse_mode="HTML")]
        for p in photos[1:]: media.append(InputMediaPhoto(media=p))
        await m.answer_media_group(media); await m.answer("Отправить альбом?", reply_markup=kb)
    await state.set_state(PostCreation.waiting_for_confirm)

@dp.callback_query(F.data == "send_post", PostCreation.waiting_for_confirm)
async def broadcast_done(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data(); text, photos = data['full_text'], data.get("photos", [])
    sent = 0
    for cid in status_messages.keys():
        try:
            if not photos: await bot.send_message(cid, text, parse_mode="HTML")
            elif len(photos) == 1: await bot.send_photo(cid, photos[0], caption=text, parse_mode="HTML")
            else:
                media = [InputMediaPhoto(media=photos[0], caption=text, parse_mode="HTML")]
                for p in photos[1:]: media.append(InputMediaPhoto(media=p))
                await bot.send_media_group(cid, media)
            sent += 1
        except: pass
    await cb.message.answer(f"✅ Отправлено в {sent} чатов."); await state.clear(); await cb.answer()

@dp.callback_query(F.data == "cancel_post")
async def cancel_upd(cb: types.CallbackQuery, state: FSMContext):
    await state.clear(); await cb.message.answer("Отменено."); await cb.answer()

# --- Базовые команды ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer(f"<b>Бот Улья</b>\nОбщих рестартов: {total_restarts}", parse_mode="HTML")

@dp.message(Command("information"))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(cid, status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить рестарты за сессию", callback_data="ask_reset")]]))
    status_messages[cid] = msg.message_id
    try: await bot.pin_chat_message(cid, msg.message_id, disable_notification=True)
    except: pass
    await save_data()

@dp.callback_query(F.data == "ask_reset")
async def ask_res(cb: types.CallbackQuery):
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚠️ ПОДТВЕРДИТЬ СБРОС", callback_data="confirm_reset")]]))
    await asyncio.sleep(5)
    try: await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить рестарты за сессию", callback_data="ask_reset")]]))
    except: pass

@dp.callback_query(F.data == "confirm_reset")
async def conf_res(cb: types.CallbackQuery):
    global session_restarts
    session_restarts = 0; await save_data(); await cb.answer("Сброшено!"); await refresh_panels()

@dp.message(Command("add"))
async def c_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return
    notifications.setdefault(args[1], []).append(f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}")
    await save_data(); await m.answer("✅ Добавлено")

# --- Потоки и Сигналы ---
async def handle_signal(request):
    try:
        data = await request.json(); u = data.get("username")
        if u:
            if u not in start_times: start_times[u] = time.time()
            accounts[u] = time.time(); asyncio.create_task(refresh_panels())
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def main():
    await load_data(); asyncio.create_task(monitor())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
