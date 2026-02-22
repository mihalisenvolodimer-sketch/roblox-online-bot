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

# --- Настройки ---
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))
ALLOWED_ADMIN = "Gold_mod1"

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Данные
accounts = {}      
start_times = {}   
notifications = {} 
status_messages = {}
total_restarts = 0     
session_restarts = 0   
last_text = {} 

def logger(msg):
    print(f"DEBUG [{time.strftime('%H:%M:%S')}]: {msg}")

class PostCreation(StatesGroup):
    waiting_for_content = State()
    waiting_for_title = State()
    waiting_for_desc = State()
    waiting_for_confirm = State()

# --- Логика Базы (Умное восстановление) ---
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
            total_restarts = data.get("restarts", 0) + 1
            session_restarts = data.get("session_restarts", 0)
            
            saved_starts = data.get("starts", {})
            saved_accounts = data.get("accounts", {}) # Последние пинги
            
            now = time.time()
            for u, l_ping in saved_accounts.items():
                # ГЛАВНОЕ УСЛОВИЕ:
                # Если с последнего пинга прошло меньше 120 сек - восстанавливаем время старта
                if now - float(l_ping) < 120:
                    accounts[u] = float(l_ping)
                    if u in saved_starts:
                        start_times[u] = float(saved_starts[u])
                else:
                    # Иначе аккаунт считается вылетевшим, время старта НЕ подтягиваем
                    logger(f"⌛ Аккаунт {u} был оффлайн слишком долго, время сброшено.")
            
            logger(f"✅ База загружена. Сессия: {session_restarts}")
    except Exception as e:
        logger(f"Ошибка загрузки: {e}")

async def save_data():
    if not db: return
    try:
        data = {
            "notifs": notifications, 
            "msgs": status_messages, 
            "restarts": total_restarts,               
            "session_restarts": session_restarts,     
            "starts": start_times,
            "accounts": accounts # Сохраняем последние пинги для проверки при рестарте
        }
        await db.set("BSS_V37_STABLE_FINAL", json.dumps(data))
    except: pass

# --- Интерфейс ---
def get_status_text():
    now = time.time()
    res = f"<b>🐝 Статус Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')} | 🔄 Рестартов: {session_restarts}\n\n"
    res += "<blockquote>"
    if not accounts:
        res += "Нет активных аккаунтов..."
    else:
        for u in sorted(accounts.keys()):
            s_time = start_times.get(u, now)
            dur = int(now - s_time)
            res += f"🟢 <code>{u}</code> | <b>{dur//3600}ч {(dur%3600)//60}м</b>\n"
    res += "</blockquote>"
    return res

async def refresh_panels():
    txt = get_status_text()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить рестарты сессии", callback_data="ask_reset")]])
    for cid, mid in list(status_messages.items()):
        if last_text.get(str(cid)) == txt: continue
        try:
            await bot.edit_message_text(txt, str(cid), int(mid), parse_mode="HTML", reply_markup=kb)
            last_text[str(cid)] = txt
        except: pass

# --- Команды Пингов ---
@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2:
        return await m.answer("Формат: <code>/add ник @тег</code>", parse_mode="HTML")
    
    acc = args[1]
    # Если тег не указан, берем автора сообщения
    tag = args[2] if len(args) > 2 else (f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}")
    
    notifications.setdefault(acc, [])
    if tag not in notifications[acc]:
        notifications[acc].append(tag)
        await save_data()
        await m.answer(f"✅ Для <b>{acc}</b> добавлен пинг {tag}", parse_mode="HTML")

@dp.message(Command("remove"))
async def cmd_remove(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return
    acc, tag = args[1], (args[2] if len(args) > 2 else f"@{m.from_user.username}")
    if acc in notifications and tag in notifications[acc]:
        notifications[acc].remove(tag)
        if not notifications[acc]: del notifications[acc]
        await save_data(); await m.answer(f"❌ Пинг {tag} убран.")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Пингов нет.")
    res = "<b>Настройки пингов:</b>\n"
    for acc, tags in notifications.items():
        res += f"• <code>{acc}</code>: {', '.join(tags)}\n"
    await m.answer(res, parse_mode="HTML")

# --- Мониторинг ---
async def monitor():
    while True:
        now = time.time()
        for u in list(accounts.keys()):
            if now - accounts[u] > 120:
                if u in notifications:
                    tags = " ".join(notifications[u])
                    msg = f"🚨 <b>ВЫЛЕТ!</b>\n\n<blockquote>👤 <code>{u}</code>\n🔔 {tags}</blockquote>"
                    for cid in status_messages:
                        try: await bot.send_message(cid, msg, parse_mode="HTML")
                        except: pass
                accounts.pop(u, None)
                start_times.pop(u, None) # Чистим время старта только при реальном вылете
        await refresh_panels()
        await save_data()
        await asyncio.sleep(30)

# --- Обработка сигналов ---
async def handle_signal(request):
    try:
        data = await request.json(); u = data.get("username")
        if u:
            # Если аккаунт не был в списке активных — значит он только что зашел
            if u not in accounts:
                # Если его нет и в start_times — это новый запуск
                if u not in start_times:
                    start_times[u] = time.time()
            
            accounts[u] = time.time()
            asyncio.create_task(refresh_panels())
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

# --- Стандартные команды ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer("<b>Бот Улья v49</b>\n/information - Панель\n/add [Ник] [Пинг]\n/list - Настройки\n/Update - Рассылка", parse_mode="HTML")

@dp.message(Command("information"))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(cid, status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить рестарты сессии", callback_data="ask_reset")]]))
    status_messages[cid] = msg.message_id
    try: await bot.pin_chat_message(cid, msg.message_id, disable_notification=True)
    except: pass
    await save_data()

@dp.callback_query(F.data == "ask_reset")
async def ask_res(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚠️ СБРОСИТЬ?", callback_data="confirm_reset")]])
    await cb.message.edit_reply_markup(reply_markup=kb)
    await asyncio.sleep(5)
    try: await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить рестарты", callback_data="ask_reset")]]))
    except: pass

@dp.callback_query(F.data == "confirm_reset")
async def conf_res(cb: types.CallbackQuery):
    global session_restarts
    session_restarts = 0; await save_data(); await cb.answer("Сессия обнулена!"); await refresh_panels()

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
        await state.set_state(PostCreation.waiting_for_title)
        await cb.message.answer("Заголовок:")
    else:
        await state.set_state(PostCreation.waiting_for_content)
        await cb.message.answer("Текст:")
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
        await state.update_data(title=txt.upper()); await state.set_state(PostCreation.waiting_for_desc)
        await m.answer("Описание:")
    else:
        d = await state.get_data()
        final = f"📢 <b>{d.get('title')}</b>\n\n{txt}" if d.get('title') else f"📢 {txt}"
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
            else:
                media = [InputMediaPhoto(media=photos[0], caption=text, parse_mode="HTML")] + [InputMediaPhoto(media=p) for p in photos[1:]]
                await bot.send_media_group(cid, media)
        except: pass
    await cb.message.answer("🚀 Отправлено!"); await state.clear(); await cb.answer()

@dp.callback_query(F.data == "no")
async def no_send(cb: types.CallbackQuery, state: FSMContext):
    await state.clear(); await cb.message.answer("Отменено."); await cb.answer()

# --- Запуск ---
async def main():
    await load_data()
    asyncio.create_task(monitor())
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
