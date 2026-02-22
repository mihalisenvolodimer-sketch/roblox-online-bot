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

# Глобальные переменные
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

# --- База Данных ---
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
            logger(f"✅ Данные загружены. Общих рестартов: {total_restarts}")
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

# --- Панель ---
def get_status_text():
    now = time.time()
    res = f"<b>🐝 Статус Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')} | 🔄 Рестартов: {session_restarts}\n\n"
    res += "<blockquote>"
    if not accounts:
        res += "Ожидание сигналов от аккаунтов..."
    else:
        for u in sorted(accounts.keys()):
            s_time = start_times.get(u, now)
            dur = int(now - s_time)
            h, m = dur//3600, (dur%3600)//60
            res += f"🟢 <code>{u}</code> | <b>{h}ч {m}м</b>\n"
    res += "</blockquote>"
    return res

async def refresh_panels():
    text = get_status_text()
    for cid, mid in list(status_messages.items()):
        if last_text.get(str(cid)) == text: continue
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить рестарты", callback_data="ask_reset")]])
            await bot.edit_message_text(text, str(cid), int(mid), parse_mode="HTML", reply_markup=kb)
            last_text[str(cid)] = text
        except: pass

# --- Команды управления ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    help_text = (
        "<b>🐝 Бот мониторинга Улья</b>\n\n"
        "📊 <b>Общие команды:</b>\n"
        "/information - Вызвать панель статуса\n"
        "/add [ник] - Подписаться на уведомления о вылете\n"
        "/remove [ник] - Отписаться от уведомлений\n"
        "/list - Список ваших подписок\n\n"
        "📢 <b>Для админа:</b>\n"
        "/Update - Рассылка (текст/фото/цитаты)\n"
        "/testdisconect [ник] - Тест вылета\n\n"
        f"📈 <i>Всего рестартов за всё время: {total_restarts}</i>"
    )
    await m.answer(help_text, parse_mode="HTML")

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2:
        return await m.answer("⚠️ Укажи ник аккаунта. Пример: <code>/add Player1</code>", parse_mode="HTML")
    
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    
    if acc not in notifications: notifications[acc] = []
    if tag not in notifications[acc]:
        notifications[acc].append(tag)
        await save_data()
        await m.answer(f"✅ Ты подписан на уведомления от <b>{acc}</b>", parse_mode="HTML")
    else:
        await m.answer(f"ℹ️ Ты уже подписан на <b>{acc}</b>", parse_mode="HTML")

@dp.message(Command("remove", "delete"))
async def cmd_remove(m: types.Message):
    args = m.text.split()
    if len(args) < 2:
        return await m.answer("⚠️ Укажи ник. Пример: <code>/remove Player1</code>", parse_mode="HTML")
    
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    
    if acc in notifications and tag in notifications[acc]:
        notifications[acc].remove(tag)
        if not notifications[acc]: del notifications[acc]
        await save_data()
        await m.answer(f"❌ Ты отписался от уведомлений <b>{acc}</b>", parse_mode="HTML")
    else:
        await m.answer(f"❓ Ты не был подписан на <b>{acc}</b>", parse_mode="HTML")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    my_subs = [acc for acc, tags in notifications.items() if tag in tags]
    
    if not my_subs:
        return await m.answer("📜 У тебя пока нет активных подписок.")
    
    res = "<b>📜 Твои подписки:</b>\n" + "\n".join([f"• <code>{a}</code>" for a in my_subs])
    await m.answer(res, parse_mode="HTML")

# --- Монитор и Пинги ---
async def monitor():
    while True:
        now = time.time()
        for u in list(accounts.keys()):
            if now - accounts[u] > 120: # 2 минуты
                if u in notifications:
                    tags = " ".join(notifications[u])
                    msg = f"🚨 <b>ВЫЛЕТ АККАУНТА!</b>\n\n<blockquote>👤 Аккаунт: <code>{u}</code>\n🔔 Пинг: {tags}</blockquote>"
                    for cid in status_messages:
                        try: await bot.send_message(cid, msg, parse_mode="HTML")
                        except: pass
                accounts.pop(u, None)
                start_times.pop(u, None)
        await refresh_panels()
        await save_data()
        await asyncio.sleep(30)

# --- Остальной функционал (Update, Тесты, Сигналы) ---
# (Код /Update, /testdisconect и Web-сервера остается таким же надежным)

@dp.message(Command("information"))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(cid, status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить рестарты", callback_data="ask_reset")]]))
    status_messages[cid] = msg.message_id
    try: await bot.pin_chat_message(cid, msg.message_id, disable_notification=True)
    except: pass
    await save_data()

@dp.callback_query(F.data == "ask_reset")
async def ask_res(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚠️ ПОДТВЕРДИТЬ СБРОС", callback_data="confirm_reset")]])
    await cb.message.edit_reply_markup(reply_markup=kb)
    await asyncio.sleep(5)
    try: await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить рестарты", callback_data="ask_reset")]]))
    except: pass

@dp.callback_query(F.data == "confirm_reset")
async def conf_res(cb: types.CallbackQuery):
    global session_restarts
    session_restarts = 0
    await save_data(); await cb.answer("Рестарты сессии сброшены!"); await refresh_panels()

@dp.message(Command("testdisconect"))
async def cmd_td(m: types.Message):
    if m.from_user.username != ALLOWED_ADMIN: return
    args = m.text.split()
    if len(args) > 1 and args[1] in accounts:
        accounts[args[1]] = time.time() - 150
        await m.answer(f"🧪 Имитация вылета {args[1]}...")

@dp.message(Command("Update"))
async def cmd_update(m: types.Message, state: FSMContext):
    if m.from_user.username != ALLOWED_ADMIN: return
    await state.set_data({"photos": []})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📝 С названием", callback_data="u_t"), InlineKeyboardButton(text="📄 Без", callback_data="u_s")]])
    await m.answer("<b>Создание рассылки:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("u_"))
async def u_choice(cb: types.CallbackQuery, state: FSMContext):
    if cb.data == "u_t":
        await state.set_state(PostCreation.waiting_for_title)
        await cb.message.answer("Введите заголовок:")
    else:
        await state.set_state(PostCreation.waiting_for_content)
        await cb.message.answer("Введите текст:")
    await cb.answer()

@dp.message(PostCreation.waiting_for_title, F.text | F.photo)
@dp.message(PostCreation.waiting_for_content, F.text | F.photo)
@dp.message(PostCreation.waiting_for_desc, F.text | F.photo)
async def collect(m: types.Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    if m.photo: photos.append(m.photo[-1].file_id); await state.update_data(photos=photos)
    txt = m.html_text or m.caption
    st = await state.get_state()
    
    if st == PostCreation.waiting_for_title:
        await state.update_data(title=txt.upper())
        await state.set_state(PostCreation.waiting_for_desc)
        await m.answer("Введите описание:")
    else:
        d = await state.get_data()
        final = f"📢 <b>{d.get('title')}</b>\n\n{txt}" if d.get('title') else f"📢 {txt}"
        await state.update_data(full_text=final)
        # Превью и кнопки отправки...
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
    await cb.message.answer("🚀 Разослано!"); await state.clear(); await cb.answer()

@dp.callback_query(F.data == "no")
async def no_send(cb: types.CallbackQuery, state: FSMContext):
    await state.clear(); await cb.message.answer("Отменено."); await cb.answer()

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
