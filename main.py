import os
import asyncio
import time
import json
import random
import logging
import sys
import io
import aiohttp
import redis.asyncio as redis
from PIL import Image, ImageDraw, ImageFont
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, BufferedInputFile
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiohttp import web

# --- Настройка логирования ---
log_format = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.INFO)
stdout_handler.setFormatter(log_format)
stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setLevel(logging.ERROR)
stderr_handler.setFormatter(log_format)

logger = logging.getLogger("BSS_PRO")
logger.setLevel(logging.INFO)
logger.addHandler(stdout_handler)
logger.addHandler(stderr_handler)

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

QUOTES = [
    "🐝 Пчёлы не спят, они фармят!",
    "🍯 Мёд сам себя не соберёт.",
    "🚀 Удачного фарма, легенда!",
    "⭐ Твой Улей — твои правила.",
    "🛡️ Мониторинг на страже профита."
]

BG_URLS = [
    "https://images.unsplash.com/photo-1557683316-973673baf926?w=800&q=80",
    "https://images.unsplash.com/photo-1579546929518-9e396f3cc809?w=800&q=80",
    "https://images.unsplash.com/photo-1557682250-33bd709cbe85?w=800&q=80"
]

class PostCreation(StatesGroup):
    waiting_for_content, waiting_for_title, waiting_for_desc, waiting_for_confirm = State(), State(), State(), State()

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
                if now - float(l_ping) < 120:
                    accounts[u] = float(l_ping)
                    if u in saved_starts: start_times[u] = float(saved_starts[u])
    except Exception as e:
        logger.error(f"Ошибка БД: {e}")

async def save_data():
    if not db: return
    try:
        await db.set("BSS_V37_STABLE_FINAL", json.dumps({
            "notifs": notifications, "msgs": status_messages,
            "total_restarts": total_restarts, "session_restarts": session_restarts,
            "starts": start_times, "accounts": accounts
        }))
    except: pass

# --- Интерфейс ---
def get_status_text():
    now = time.time()
    text = f"<b>🐝 Состояние Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')} | 🔄 Сессия: {session_restarts}\n\n<blockquote>"
    if not accounts: text += "<i>Ожидание сигналов...</i>"
    else:
        for u in sorted(accounts.keys()):
            dur = int(now - start_times.get(u, now))
            text += f"🟢 <code>{u}</code> | <b>{dur//3600}ч {(dur%3600)//60}м {dur%60}с</b>\n"
    text += f"</blockquote>\n<i>{random.choice(QUOTES)}</i>"
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
            if "not modified" not in str(e).lower(): logger.warning(f"Панель {cid} не обновлена: {e}")

# --- API Роблокс (Аватарки) ---
async def get_roblox_avatar(username, session):
    try:
        # Получаем ID игрока по нику
        async with session.post("https://users.roblox.com/v1/usernames/users", json={"usernames": [username], "excludeBannedUsers": False}) as resp:
            data = await resp.json()
            if not data.get("data"): return None
            user_id = data["data"][0]["id"]
        
        # Получаем URL аватарки (голова)
        url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=150x150&format=Png&isCircular=true"
        async with session.get(url) as resp:
            data = await resp.json()
            if not data.get("data"): return None
            img_url = data["data"][0]["imageUrl"]
        
        # Скачиваем саму картинку
        async with session.get(img_url) as resp:
            img_bytes = await resp.read()
            return Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    except:
        return None

# --- Генерация Картинки ---
async def generate_status_image(target_accounts, is_online_mode=True):
    width = 600
    row_height = 80
    header_height = 100
    footer_height = 80
    height = header_height + (len(target_accounts) * row_height) + footer_height
    if len(target_accounts) == 0: height = header_height + row_height + footer_height

    # Пытаемся загрузить фон
    img = Image.new("RGBA", (width, height), (30, 30, 30, 255))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(random.choice(BG_URLS)) as resp:
                bg_bytes = await resp.read()
                bg = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")
                bg = bg.resize((width, height))
                img.paste(bg, (0, 0))
    except: pass # Если не вышло, останется серый фон

    draw = ImageDraw.Draw(img)
    # Попытка использовать дефолтный шрифт (или подгрузить встроенный)
    try: font_large = ImageFont.truetype("arial.ttf", 36)
    except: font_large = ImageFont.load_default()
    try: font_medium = ImageFont.truetype("arial.ttf", 24)
    except: font_medium = ImageFont.load_default()
    try: font_small = ImageFont.truetype("arial.ttf", 18)
    except: font_small = ImageFont.load_default()

    # Заголовок
    title = "🐝 Текущий онлайн макросов" if is_online_mode else "🚀 Будут стоять на макросе"
    draw.text((30, 30), title, font=font_large, fill=(255, 255, 255, 255))

    if not target_accounts:
        draw.text((30, header_height + 20), "Никого нет в сети...", font=font_medium, fill=(200, 200, 200, 255))
    else:
        async with aiohttp.ClientSession() as session:
            now = time.time()
            for i, acc in enumerate(target_accounts):
                y_pos = header_height + (i * row_height)
                
                # Плашка под аккаунт
                draw.rectangle([20, y_pos, width-20, y_pos+row_height-10], fill=(0, 0, 0, 150), radius=10)
                
                # Скачиваем аватарку
                avatar = await get_roblox_avatar(acc, session)
                if avatar:
                    avatar = avatar.resize((50, 50))
                    img.paste(avatar, (30, y_pos + 10), avatar)
                
                # Текст (Ник)
                draw.text((95, y_pos + 20), acc, font=font_medium, fill=(255, 255, 255, 255))
                
                # Текст (Статус/Время)
                if is_online_mode:
                    s_time = start_times.get(acc, now)
                    dur = int(now - s_time)
                    status_text = f"{dur//3600}h {(dur%3600)//60}m {dur%60}s"
                    draw.text((width - 180, y_pos + 20), status_text, font=font_medium, fill=(100, 255, 100, 255))
                else:
                    draw.text((width - 150, y_pos + 20), "Ожидается", font=font_medium, fill=(255, 200, 100, 255))

    # Цитата
    draw.text((30, height - 50), random.choice(QUOTES), font=font_small, fill=(200, 200, 200, 255))

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    return img_byte_arr.getvalue()

# --- Команды ---
@dp.message(Command("start", "information"))
async def cmd_info(m: types.Message):
    if m.text.startswith("/start"):
        await m.answer(f"<b>🐝 Улей BSS v53</b>\nРестартов сессии: {session_restarts}\nВсего: {total_restarts}\n\n/img - Сгенерировать карточку\n/testdisconect [Ник] - Тест", parse_mode="HTML")
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(chat_id=cid, message_id=status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Сбросить сессию", callback_data="ask_reset")]]))
    status_messages[cid] = msg.message_id
    try: await bot.pin_chat_message(chat_id=cid, message_id=msg.message_id, disable_notification=True)
    except: pass
    await save_data()

@dp.message(Command("add", "remove", "list"))
async def cmd_ping_settings(m: types.Message):
    cmd = m.text.split()[0].lower()
    if cmd == "/list":
        if not notifications: return await m.answer("Список пингов пуст.")
        return await m.answer("<b>📜 Пинги:</b>\n" + "".join([f"• <code>{k}</code>: {', '.join(v)}\n" for k,v in notifications.items()]), parse_mode="HTML")
    
    args = m.text.split()
    if len(args) < 2: return await m.answer("Укажи ник!")
    acc = args[1]
    tag = args[2] if len(args) > 2 else (f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}")
    
    notifications.setdefault(acc, [])
    if cmd == "/add" and tag not in notifications[acc]:
        notifications[acc].append(tag); await save_data(); await m.answer(f"✅ Добавлен пинг: {acc} -> {tag}")
    elif cmd in ["/remove", "/delete"] and tag in notifications[acc]:
        notifications[acc].remove(tag)
        if not notifications[acc]: del notifications[acc]
        await save_data(); await m.answer(f"❌ Пинг удален: {acc} -> {tag}")

@dp.message(Command("img"))
async def cmd_img(m: types.Message):
    args = m.text.split()[1:]
    is_online = len(args) == 0
    target_accounts = list(accounts.keys()) if is_online else args
    
    msg = await m.answer("🎨 Генерирую карточку, скачиваю аватарки...")
    image_bytes = await generate_status_image(target_accounts, is_online_mode=is_online)
    photo = BufferedInputFile(image_bytes, filename="status.png")
    await bot.send_photo(m.chat.id, photo)
    await msg.delete()

@dp.message(Command("testdisconect"))
async def cmd_test(m: types.Message):
    if m.from_user.username != ALLOWED_ADMIN: return
    args = m.text.split()
    if len(args) > 1:
        target = args[1]
        if target in accounts:
            accounts[target] = time.time() - 300 # Сдвигаем время на "5 минут назад"
            if target not in notifications:
                await m.answer(f"⚠️ У <code>{target}</code> нет пингов, но тестовое уведомление все равно придет.", parse_mode="HTML")
            await m.answer(f"🧪 Имитация вылета <code>{target}</code> начата. Жди до 30 сек.", parse_mode="HTML")
        else: await m.answer(f"Аккаунт <code>{target}</code> не в сети.", parse_mode="HTML")

# --- Рассылка (Упрощенный код для экономии места) ---
@dp.message(Command("Update"))
async def cmd_update(m: types.Message, state: FSMContext):
    if m.from_user.username != ALLOWED_ADMIN: return
    await state.set_data({"photos": []}); await m.answer("Тип:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="С названием", callback_data="u_t"), InlineKeyboardButton(text="Без", callback_data="u_s")]]))

@dp.callback_query(F.data.startswith("u_"))
async def u_choice(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(PostCreation.waiting_for_title if cb.data == "u_t" else PostCreation.waiting_for_content)
    await cb.message.answer("Жду текст:"); await cb.answer()

@dp.message(PostCreation.waiting_for_title, F.text | F.photo)
@dp.message(PostCreation.waiting_for_content, F.text | F.photo)
@dp.message(PostCreation.waiting_for_desc, F.text | F.photo)
async def collect(m: types.Message, state: FSMContext):
    d = await state.get_data(); photos = d.get("photos", [])
    if m.photo: photos.append(m.photo[-1].file_id); await state.update_data(photos=photos)
    txt = m.html_text or m.caption
    if await state.get_state() == PostCreation.waiting_for_title:
        await state.update_data(title=txt.upper()); await state.set_state(PostCreation.waiting_for_desc); await m.answer("Описание:")
    else:
        final = f"📢 <b>{d.get('title')}</b>\n\n{txt}" if d.get('title') else f"📢 {txt}"
        await state.update_data(full_text=final); await state.set_state(PostCreation.waiting_for_confirm)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ ОТПРАВИТЬ", callback_data="go")]])
        if not photos: await m.answer(final, parse_mode="HTML", reply_markup=kb)
        else: await m.answer_photo(photos[0], caption=final, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "go", PostCreation.waiting_for_confirm)
async def go_send(cb: types.CallbackQuery, state: FSMContext):
    d = await state.get_data(); text, photos = d['full_text'], d.get("photos", [])
    for cid in status_messages:
        try:
            if not photos: await bot.send_message(cid, text, parse_mode="HTML")
            else: await bot.send_photo(cid, photos[0], caption=text, parse_mode="HTML")
        except: pass
    await cb.message.answer("🚀 Готово!"); await state.clear(); await cb.answer()

# --- Логика Сервера ---
@dp.callback_query(F.data == "ask_reset")
async def ask_res(cb: types.CallbackQuery): await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚠️ ТЫ УВЕРЕН?", callback_data="confirm_reset")]]))

@dp.callback_query(F.data == "confirm_reset")
async def conf_res(cb: types.CallbackQuery):
    global session_restarts; session_restarts = 0; await save_data(); await cb.answer("Сброшено!"); await refresh_panels()

async def handle_signal(request):
    try:
        data = await request.json(); u = data.get("username")
        if u:
            now = time.time()
            if u not in start_times: start_times[u] = now
            accounts[u] = now; return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def monitor():
    while True:
        try:
            now = time.time()
            for u in list(accounts.keys()):
                if now - accounts[u] > 120:
                    # ИСПРАВЛЕНИЕ: Гарантированная отправка сообщения
                    tags = " ".join(notifications.get(u, ["<i>(без пинга)</i>"]))
                    for cid in status_messages:
                        try: await bot.send_message(cid, f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{tags}", parse_mode="HTML")
                        except: pass
                    accounts.pop(u, None); start_times.pop(u, None)
            await refresh_panels(); await save_data()
        except Exception as e: logger.error(f"Ошибка монитора: {e}")
        await asyncio.sleep(30)

async def main():
    logger.info("Бот запускается...")
    await load_data(); asyncio.create_task(monitor())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await bot.delete_webhook(drop_pending_updates=True); await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
