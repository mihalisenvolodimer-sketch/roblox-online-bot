import os
import asyncio
import time
import json
import random
import logging
import sys
import io
import aiohttp
import datetime
from datetime import timedelta, timezone
import redis.asyncio as redis
from PIL import Image, ImageDraw, ImageFont
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiohttp import web

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("BSS_PRO")

# --- Конфигурация ---
VERSION = "V3.1" # Патч-версия
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))
ALLOWED_ADMIN = "Gold_mod1"
FONT_PATH = "roboto_font.ttf"
FONT_URL = "https://cdn.jsdelivr.net/gh/googlefonts/roboto@main/src/hinted/Roboto-Bold.ttf"

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Глобальные данные
accounts, start_times, notifications, status_messages = {}, {}, {}, {}
total_restarts, session_restarts = 0, 0

QUOTES = ["Пчёлы не спят, они фармят!", "Мёд сам себя не соберёт.", "Удачного фарма, легенда!", "Мониторинг на страже."]
BG_URLS = [
    "https://wallpaperaccess.com/full/7500647.png",
    "https://wallpaperaccess.com/full/14038149.jpg",
    "https://wallpaperaccess.com/full/14038208.jpg",
    "https://wallpaperaccess.com/full/8221067.png",
    "https://wallpaperaccess.com/full/8221104.png"
]

class PostCreation(StatesGroup):
    waiting_for_content, waiting_for_title, waiting_for_desc, waiting_for_confirm = State(), State(), State(), State()

# --- Вспомогательные функции ---
async def download_font():
    if not os.path.exists(FONT_PATH):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(FONT_URL, timeout=20) as r:
                    if r.status == 200:
                        with open(FONT_PATH, "wb") as f: f.write(await r.read())
                        logger.info("Шрифт Roboto успешно загружен.")
        except Exception as e: logger.error(f"Ошибка шрифта: {e}")

async def get_roblox_avatar(username, session):
    try:
        async with session.post("https://users.roblox.com/v1/usernames/users", json={"usernames": [username], "excludeBannedUsers": False}) as resp:
            data = await resp.json()
            if not data.get("data"): return None
            uid = data["data"][0]["id"]
        url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={uid}&size=150x150&format=Png&isCircular=true"
        async with session.get(url) as resp:
            data = await resp.json()
            img_url = data["data"][0]["imageUrl"]
        async with session.get(img_url) as resp:
            return Image.open(io.BytesIO(await resp.read())).convert("RGBA")
    except: return None

# --- Работа с БД ---
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
            saved_accounts = data.get("accounts", {})
            now = time.time()
            for u, l_ping in saved_accounts.items():
                if now - float(l_ping) < 120:
                    accounts[u] = float(l_ping)
                    if u in data.get("starts", {}): start_times[u] = float(data["starts"][u])
    except Exception as e: logger.error(f"Ошибка БД: {e}")

async def save_data():
    if not db: return
    try:
        await db.set("BSS_V37_STABLE_FINAL", json.dumps({
            "notifs": notifications, "msgs": status_messages, "total_restarts": total_restarts,
            "session_restarts": session_restarts, "starts": start_times, "accounts": accounts
        }))
    except: pass

# --- Визуализация ---
async def generate_status_image(target_accounts, is_online_mode=True):
    width, row_h, head_h, foot_h = 750, 100, 130, 80
    height = head_h + (max(1, len(target_accounts)) * row_h) + foot_h
    img = Image.new("RGBA", (width, height), (30, 30, 30, 255))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(random.choice(BG_URLS)) as r:
                bg = Image.open(io.BytesIO(await r.read())).convert("RGBA")
                bg_w, bg_h = bg.size
                ratio = max(width/bg_w, height/bg_h)
                bg = bg.resize((int(bg_w*ratio), int(bg_h*ratio)), Image.LANCZOS)
                img.paste(bg, (0, 0))
    except: pass
    draw = ImageDraw.Draw(img)
    try:
        f_l, f_m, f_s = ImageFont.truetype(FONT_PATH, 42), ImageFont.truetype(FONT_PATH, 28), ImageFont.truetype(FONT_PATH, 20)
    except: f_l = f_m = f_s = ImageFont.load_default()
    title = "ОНЛАЙН МОНИТОРИНГ" if is_online_mode else "ПЛАН МАКРОСА"
    draw.text((45, 45), title, font=f_l, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0,0,0))
    if target_accounts:
        async with aiohttp.ClientSession() as session:
            for i, acc in enumerate(target_accounts):
                y = head_h + (i * row_h)
                draw.rounded_rectangle([35, y, width-35, y+row_h-15], fill=(0, 0, 0, 170), radius=15)
                av = await get_roblox_avatar(acc, session)
                if av: img.paste(av.resize((70, 70)), (50, y+8), av.resize((70, 70)))
                draw.text((140, y+22), acc, font=f_m, fill=(255, 255, 255))
                if is_online_mode:
                    dur = int(time.time() - start_times.get(acc, time.time()))
                    draw.text((width-200, y+22), f"{dur//3600}ч {(dur%3600)//60}м", font=f_m, fill=(100, 255, 100))
                else: draw.text((width-210, y+22), "ОЖИДАНИЕ", font=f_m, fill=(255, 180, 50))
    draw.text((45, height-50), random.choice(QUOTES), font=f_s, fill=(220, 220, 220), stroke_width=1, stroke_fill=(0,0,0))
    buf = io.BytesIO(); img.save(buf, format='PNG'); return buf.getvalue()

def get_status_text():
    tz_gmt2 = timezone(timedelta(hours=2))
    now_str = datetime.datetime.now(tz_gmt2).strftime("%H:%M:%S")
    text = f"<b>🐝 Состояние Улья BSS</b>\n🕒 Время (GMT+2): <b>{now_str}</b>\n🔄 Сессия: {session_restarts}\n\n<blockquote>"
    if not accounts: text += "Ожидание сигналов..."
    else:
        for u in sorted(accounts.keys()):
            d = int(time.time() - start_times.get(u, time.time()))
            text += f"🟢 <code>{u}</code> | <b>{d//3600}ч {(d%3600)//60}м</b>\n"
    return text + "</blockquote>"

# --- ОБРАБОТЧИКИ КОМАНД ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer(f"<b>🐝 Улей BSS {VERSION}</b>\n\n<b>Команды:</b>\n/information — Панель\n/list — Пинги\n/add [Ник] [Тег] — Добавить\n/remove [Ник] [Тег] — Удалить", parse_mode="HTML")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("📜 Список пингов пуст.")
    res = "<b>📜 Настройки пингов:</b>\n"
    for acc, tags in notifications.items():
        res += f"• <code>{acc}</code>: {', '.join(tags)}\n"
    await m.answer(res, parse_mode="HTML")

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Использование: <code>/add Ник @тег</code>", parse_mode="HTML")
    acc, tag = args[1], args[2] if len(args) > 2 else (f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}")
    if acc not in notifications: notifications[acc] = []
    if tag not in notifications[acc]: 
        notifications[acc].append(tag)
        await save_data(); await m.answer(f"✅ Пинг добавлен для <b>{acc}</b>", parse_mode="HTML")
    else: await m.answer(f"ℹ️ Тег уже существует.")

@dp.message(Command("remove"))
async def cmd_remove(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Использование: <code>/remove Ник</code>", parse_mode="HTML")
    acc, tag = args[1], args[2] if len(args) > 2 else None
    if acc in notifications:
        if not tag: del notifications[acc]
        elif tag in notifications[acc]:
            notifications[acc].remove(tag)
            if not notifications[acc]: del notifications[acc]
        await save_data(); await m.answer(f"❌ Пинг для {acc} удален.")
    else: await m.answer("Ник не найден.")

@dp.message(Command("testdisconect"))
async def cmd_test(m: types.Message):
    if m.from_user.username != ALLOWED_ADMIN: return
    args = m.text.split()
    if len(args) > 1 and args[1] in accounts:
        accounts[args[1]] = time.time() - 300 # Ставим время 5 минут назад
        await m.answer(f"🧪 Тестирую вылет {args[1]}...")
        await check_timeouts()
    else: await m.answer("Укажите ник, который сейчас в сети.")

@dp.message(Command("information"))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(chat_id=cid, message_id=status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Обновить / Сброс", callback_data="ask_reset")]]))
    status_messages[cid] = msg.message_id
    try: await bot.pin_chat_message(chat_id=cid, message_id=msg.message_id, disable_notification=True)
    except: pass
    await save_data()

@dp.message(Command("img"))
async def cmd_img(m: types.Message):
    args = m.text.split()[1:]
    is_on = len(args) == 0
    t_accs = list(accounts.keys()) if is_on else args
    msg = await m.answer("🎨 Отрисовываю...")
    try:
        img_bytes = await generate_status_image(t_accs, is_on)
        await bot.send_photo(m.chat.id, BufferedInputFile(img_bytes, filename="bss.png"))
        await msg.delete()
    except Exception as e: await msg.edit_text(f"Ошибка: {e}")

@dp.message(Command("Update"))
async def cmd_update(m: types.Message, state: FSMContext):
    if m.from_user.username != ALLOWED_ADMIN: return
    await state.set_data({"photos": []})
    await m.answer("Тип новости:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="С заголовком", callback_data="u_t"), InlineKeyboardButton(text="Без заголовка", callback_data="u_s")]]))

@dp.callback_query(F.data.startswith("u_"))
async def u_choice(cb: types.CallbackQuery, state: FSMContext):
    if cb.data == "u_t": await state.set_state(PostCreation.waiting_for_title); await cb.message.answer("ЗАГОЛОВОК:")
    else: await state.set_state(PostCreation.waiting_for_content); await cb.message.answer("ТЕКСТ:")
    await cb.answer()

@dp.message(PostCreation.waiting_for_title, F.text | F.photo)
@dp.message(PostCreation.waiting_for_content, F.text | F.photo)
@dp.message(PostCreation.waiting_for_desc, F.text | F.photo)
async def collect_post(m: types.Message, state: FSMContext):
    d = await state.get_data(); photos = d.get("photos", [])
    if m.photo: photos.append(m.photo[-1].file_id); await state.update_data(photos=photos)
    txt = m.html_text or m.caption or ""
    st = await state.get_state()
    if st == PostCreation.waiting_for_title:
        await state.update_data(title=txt.upper()); await state.set_state(PostCreation.waiting_for_desc); await m.answer("ОПИСАНИЕ:")
    else:
        final = f"📢 <b>{d.get('title')}</b>\n\n{txt}" if d.get('title') else f"📢 {txt}"
        await state.update_data(full_text=final); await state.set_state(PostCreation.waiting_for_confirm)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ОТПРАВИТЬ ✅", callback_data="send_all")]])
        if photos: await m.answer_photo(photos[0], caption=final, parse_mode="HTML", reply_markup=kb)
        else: await m.answer(final, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "send_all")
async def process_send(cb: types.CallbackQuery, state: FSMContext):
    d = await state.get_data()
    for cid in status_messages:
        try:
            if not d.get("photos"): await bot.send_message(cid, d['full_text'], parse_mode="HTML")
            else: await bot.send_photo(cid, d["photos"][0], caption=d['full_text'], parse_mode="HTML")
        except: pass
    await cb.message.answer("✅ Готово!"); await state.clear(); await cb.answer()

@dp.callback_query(F.data == "ask_reset")
async def ask_res(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚠️ СБРОС СЕССИИ", callback_data="conf_res"), InlineKeyboardButton(text="🔄 ОБНОВИТЬ ВРЕМЯ", callback_data="refresh_only")]])
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data == "refresh_only")
async def refresh_only(cb: types.CallbackQuery): await refresh_panels(); await cb.answer("Обновлено")

@dp.callback_query(F.data == "conf_res")
async def conf_res(cb: types.CallbackQuery):
    global session_restarts; session_restarts = 0; await save_data(); await cb.answer("Сброс!"); await refresh_panels()

# --- ЯДРО МОНИТОРИНГА ---
async def check_timeouts():
    now = time.time()
    for u in list(accounts.keys()):
        if now - accounts[u] > 120:
            tags = " ".join(notifications.get(u, ["(без пинга)"]))
            for cid in status_messages:
                try: await bot.send_message(cid, f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{tags}", parse_mode="HTML")
                except: pass
            accounts.pop(u, None); start_times.pop(u, None)
    await save_data(); await refresh_panels()

async def refresh_panels():
    txt = get_status_text()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Обновить / Сброс", callback_data="ask_reset")]])
    for cid, mid in list(status_messages.items()):
        try: await bot.edit_message_text(txt, chat_id=cid, message_id=mid, parse_mode="HTML", reply_markup=kb)
        except: pass

async def handle_signal(request):
    try:
        d = await request.json(); u = d.get("username")
        if u:
            if u not in start_times: start_times[u] = time.time()
            accounts[u] = time.time(); return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def main():
    await download_font(); await load_data()
    asyncio.create_task(monitor_loop())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await bot.delete_webhook(drop_pending_updates=True); await dp.start_polling(bot)

async def monitor_loop():
    while True:
        try: await check_timeouts()
        except: pass
        await asyncio.sleep(30)

if __name__ == "__main__": asyncio.run(main())
