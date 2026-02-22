import os
import asyncio
import time
import json
import logging
import redis.asyncio as redis
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiohttp import web

# --- Настройка подробного логирования ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("BSS_Bot")

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))
ALLOWED_ADMIN = "Gold_mod1"

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Глобальные данные
accounts = {}      
start_times = {}   
notifications = {} 
status_messages = {}
total_restarts = 0     
session_restarts = 0   
last_text = {} 

class PostCreation(StatesGroup):
    waiting_for_content = State()
    waiting_for_title = State()
    waiting_for_desc = State()
    waiting_for_confirm = State()

# --- Работа с Базой Данных ---
async def load_data():
    global db, notifications, status_messages, total_restarts, session_restarts, start_times, accounts
    if not REDIS_URL:
        logger.warning("REDIS_URL не найден. Работа без базы данных.")
        return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V37_STABLE_FINAL")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            
            # Увеличиваем счетчики при запуске
            total_restarts = data.get("restarts", 0) + 1
            session_restarts = data.get("session_restarts", 0) + 1 # +1 за текущий апдейт/рестарт
            
            saved_starts = data.get("starts", {})
            saved_accounts = data.get("accounts", {})
            
            now = time.time()
            for u, l_ping in saved_accounts.items():
                # Если с последнего пинга прошло < 120 сек - восстанавливаем uptime
                if now - float(l_ping) < 120:
                    accounts[u] = float(l_ping)
                    if u in saved_starts:
                        start_times[u] = float(saved_starts[u])
                    logger.info(f"Аккаунт {u} восстановлен (uptime сохранен)")
                else:
                    logger.info(f"Аккаунт {u} был оффлайн слишком долго. Сброс времени.")
            
            logger.info(f"Данные загружены. Рестартов сессии: {session_restarts}")
            await save_data() # Сразу сохраняем обновленные счетчики
    except Exception as e:
        logger.error(f"Ошибка при загрузке БД: {e}")

async def save_data():
    if not db: return
    try:
        data = {
            "notifs": notifications, 
            "msgs": status_messages, 
            "restarts": total_restarts,               
            "session_restarts": session_restarts,     
            "starts": start_times,
            "accounts": accounts
        }
        await db.set("BSS_V37_STABLE_FINAL", json.dumps(data))
    except Exception as e:
        logger.error(f"Ошибка сохранения БД: {e}")

# --- Логика Обновления Панели ---
def get_status_text():
    now = time.time()
    res = f"<b>🐝 Статус Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')} | 🔄 Рестартов: {session_restarts}\n\n"
    res += "<blockquote>"
    if not accounts:
        res += "Аккаунты офлайн..."
    else:
        for u in sorted(accounts.keys()):
            s_time = start_times.get(u, now)
            dur = int(now - s_time)
            res += f"🟢 <code>{u}</code> | <b>{dur//3600}ч {(dur%3600)//60}м</b>\n"
    res += "</blockquote>"
    return res

async def refresh_panels():
    txt = get_status_text()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить сессию", callback_data="ask_reset")]])
    
    for cid, mid in list(status_messages.items()):
        if last_text.get(str(cid)) == txt:
            continue # Текст не изменился, не тратим лимиты
            
        try:
            await bot.edit_message_text(txt, str(cid), int(mid), parse_mode="HTML", reply_markup=kb)
            last_text[str(cid)] = txt
            logger.info(f"Панель в чате {cid} обновлена.")
        except Exception as e:
            if "message is not modified" in str(e).lower():
                last_text[str(cid)] = txt
            else:
                logger.error(f"Не удалось обновить панель {cid}: {e}")

# --- Команды ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer(f"<b>Бот запущен</b>\nРестартов сессии: {session_restarts}\nОбщих рестартов: {total_restarts}", parse_mode="HTML")

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Пример: <code>/add ник @тег</code>", parse_mode="HTML")
    acc = args[1]
    tag = args[2] if len(args) > 2 else (f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}")
    
    notifications.setdefault(acc, [])
    if tag not in notifications[acc]:
        notifications[acc].append(tag)
        await save_data()
        logger.info(f"Добавлен пинг: {acc} -> {tag}")
        await m.answer(f"✅ Пинг для <b>{acc}</b> на <b>{tag}</b> добавлен.", parse_mode="HTML")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Список пингов пуст.")
    res = "<b>Настройки пингов:</b>\n"
    for acc, tags in notifications.items():
        res += f"• <code>{acc}</code>: {', '.join(tags)}\n"
    await m.answer(res, parse_mode="HTML")

@dp.message(Command("information"))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(cid, status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить сессию", callback_data="ask_reset")]]))
    status_messages[cid] = msg.message_id
    try: await bot.pin_chat_message(cid, msg.message_id, disable_notification=True)
    except: pass
    await save_data()
    logger.info(f"Новая панель создана в чате {cid}")

@dp.callback_query(F.data == "ask_reset")
async def ask_res(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚠️ ПОДТВЕРДИТЬ СБРОС", callback_data="confirm_reset")]])
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data == "confirm_reset")
async def conf_res(cb: types.CallbackQuery):
    global session_restarts
    session_restarts = 0
    await save_data()
    await cb.answer("Счетчик сессии сброшен!")
    await refresh_panels()
    logger.info("Счетчик сессии сброшен вручную.")

# --- Цикл Мониторинга ---
async def monitor():
    while True:
        try:
            now = time.time()
            for u in list(accounts.keys()):
                if now - accounts[u] > 120:
                    if u in notifications:
                        tags = " ".join(notifications[u])
                        msg = f"🚨 <b>ВЫЛЕТ!</b>\n\n<blockquote>👤 <code>{u}</code>\n🔔 {tags}</blockquote>"
                        for cid in status_messages:
                            try: await bot.send_message(cid, msg, parse_mode="HTML")
                            except: pass
                        logger.info(f"Отправлено уведомление о вылете {u}")
                    accounts.pop(u, None)
                    start_times.pop(u, None)
            
            await refresh_panels()
            await save_data()
        except Exception as e:
            logger.error(f"Ошибка в цикле монитора: {e}")
        await asyncio.sleep(30)

# --- Web Server для сигналов ---
async def handle_signal(request):
    try:
        data = await request.json()
        u = data.get("username")
        if u:
            now = time.time()
            if u not in start_times:
                start_times[u] = now
                logger.info(f"Аккаунт {u} зашел в сеть (новый uptime).")
            
            accounts[u] = now
            return web.Response(text="OK")
    except Exception as e:
        logger.error(f"Ошибка обработки сигнала: {e}")
    return web.Response(status=400)

# --- Рассылка /Update (без изменений) ---
@dp.message(Command("Update"))
async def cmd_update(m: types.Message, state: FSMContext):
    if m.from_user.username != ALLOWED_ADMIN: return
    await state.set_data({"photos": []})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📝 Название", callback_data="u_t"), InlineKeyboardButton(text="📄 Без", callback_data="u_s")]])
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
    await cb.message.answer("🚀 Разослано!"); await state.clear(); await cb.answer()

# --- Главная функция ---
async def main():
    logger.info("Бот запускается...")
    await load_data()
    asyncio.create_task(monitor())
    
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    logger.info(f"Web-сервер запущен на порту {PORT}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
