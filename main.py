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

# --- Логирование ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("BSS_PRO")

def log_act(m, action):
    user = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    logger.info(f"[USER_ACTION] {user} -> {action}")

# --- Конфигурация ---
VERSION = "V4.8"
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
pause_data = {} 
total_restarts, session_restarts = 0, 0

QUOTES = ["Пчёлы не спят, они фармят!", "Мёд сам себя не соберёт.", "Удачного фарма, легенда!", "Мониторинг на страже."]
BG_URLS = [
    "https://wallpaperaccess.com/full/7500647.png",
    "https://wallpaperaccess.com/full/14038149.jpg",
    "https://wallpaperaccess.com/full/14038208.jpg",
    "https://wallpaperaccess.com/full/8221067.png"
]

class PostCreation(StatesGroup):
    waiting_for_content = State()

class TechPause(StatesGroup):
    choosing_target = State()
    entering_time = State()
    choosing_auto_off = State()

# --- Вспомогательные функции ---
async def download_font():
    if not os.path.exists(FONT_PATH):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(FONT_URL) as r:
                    if r.status == 200:
                        with open(FONT_PATH, "wb") as f: f.write(await r.read())
        except: pass

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

# --- РАБОТА С БД ---
async def load_data():
    global db, notifications, status_messages, total_restarts, session_restarts, start_times, accounts, pause_data
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
            pause_data = data.get("pause_data", {})
            saved_accs = data.get("accounts", {})
            now = time.time()
            for u, p in saved_accs.items():
                if now - float(p) < 120:
                    accounts[u] = float(p)
                    if u in data.get("starts", {}): start_times[u] = float(data["starts"][u])
        else: total_restarts = session_restarts = 1
    except: pass

async def save_data():
    if not db: return
    try:
        await db.set("BSS_V37_STABLE_FINAL", json.dumps({
            "notifs": notifications, "msgs": status_messages, "total_restarts": total_restarts,
            "session_restarts": session_restarts, "starts": start_times, "accounts": accounts,
            "pause_data": pause_data
        }))
    except: pass

# --- ВИЗУАЛИЗАЦИЯ (/img) ---
async def generate_status_image(target_accounts, is_online_mode=True):
    width, row_h, head_h, foot_h = 750, 100, 130, 80
    height = head_h + (max(1, len(target_accounts)) * row_h) + foot_h
    img = Image.new("RGBA", (width, height), (30, 30, 30, 255))
    
    # Загрузка случайного фона
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(random.choice(BG_URLS), timeout=5) as r:
                if r.status == 200:
                    bg = Image.open(io.BytesIO(await r.read())).convert("RGBA")
                    bg_w, bg_h = bg.size
                    ratio = max(width/bg_w, height/bg_h)
                    bg = bg.resize((int(bg_w*ratio), int(bg_h*ratio)), Image.LANCZOS)
                    img.paste(bg, (0, 0))
    except Exception as e: logger.error(f"Фон не загружен: {e}")
    
    draw = ImageDraw.Draw(img)
    try:
        f_l = ImageFont.truetype(FONT_PATH, 42)
        f_m = ImageFont.truetype(FONT_PATH, 28)
        f_s = ImageFont.truetype(FONT_PATH, 20)
    except: f_l = f_m = f_s = ImageFont.load_default()
    
    title = "ОНЛАЙН МОНИТОРИНГ" if is_online_mode else "ПЛАН МАКРОСА"
    draw.text((45, 45), title, font=f_l, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0,0,0))
    
    now = time.time()
    if target_accounts:
        async with aiohttp.ClientSession() as session:
            for i, acc in enumerate(target_accounts):
                y = head_h + (i * row_h)
                draw.rounded_rectangle([35, y, width-35, y+row_h-15], fill=(0, 0, 0, 170), radius=15)
                av = await get_roblox_avatar(acc, session)
                if av: img.paste(av.resize((70, 70)), (50, y+8), av.resize((70, 70)))
                draw.text((140, y+22), acc, font=f_m, fill=(255, 255, 255))
                
                if acc in pause_data and now < pause_data[acc]['until']:
                    draw.text((width-240, y+22), "ТЕХ. ПЕРЕРЫВ", font=f_m, fill=(255, 180, 50))
                elif is_online_mode and acc in accounts:
                    dur = int(now - start_times.get(acc, now))
                    draw.text((width-200, y+22), f"{dur//3600}ч {(dur%3600)//60}м", font=f_m, fill=(100, 255, 100))
                else:
                    draw.text((width-210, y+22), "ОЖИДАНИЕ", font=f_m, fill=(200, 200, 200))
                    
    draw.text((45, height-50), random.choice(QUOTES), font=f_s, fill=(220, 220, 220), stroke_width=1, stroke_fill=(0,0,0))
    buf = io.BytesIO(); img.save(buf, format='PNG'); return buf.getvalue()

def get_status_text():
    tz = timezone(timedelta(hours=2))
    now_str = datetime.datetime.now(tz).strftime("%H:%M:%S")
    now = time.time()
    text = f"<b>🐝 Улей BSS {VERSION}</b>\n🕒 GMT+2: <b>{now_str}</b>\n🔄 Сессия: {session_restarts}\n\n"
    acc_list = sorted(list(set(list(accounts.keys()) + list(pause_data.keys()))))
    if not acc_list: text += "<blockquote>Ожидание сигналов...</blockquote>"
    else:
        text += "<blockquote>"
        for u in acc_list:
            if u in pause_data and now < pause_data[u]['until']:
                rem = int(pause_data[u]['until'] - now)
                text += f"🛠 <code>{u}</code> | <b>ПАУЗА ({rem//60}м)</b>\n"
            elif u in accounts:
                d = int(now - start_times.get(u, now))
                text += f"🟢 <code>{u}</code> | <b>{d//3600}ч {(d%3600)//60}м</b>\n"
        text += "</blockquote>"
    return text

# --- ОСНОВНЫЕ КОМАНДЫ ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    log_act(m, "Команда /start")
    await m.answer(f"<b>🐝 Улей BSS {VERSION}</b>\n\n📊 <b>Статистика:</b>\n├ Рестартов сессии: <code>{session_restarts}</code>\n└ Всего рестартов: <code>{total_restarts}</code>\n\n<b>Команды:</b>\n/information — Панель\n/img — Статус-картинка\n/list — Пинги\n/add [Ник] [Тег]\n/remove [Ник]", parse_mode="HTML")

@dp.message(Command("img"))
async def cmd_img(m: types.Message):
    log_act(m, "Команда /img")
    args = m.text.split()[1:]
    is_on = len(args) == 0
    t_accs = list(set(list(accounts.keys()) + list(pause_data.keys()))) if is_on else args
    if not t_accs: return await m.answer("Нет аккаунтов для отрисовки.")
    
    msg = await m.answer("🎨 Отрисовываю...")
    try:
        img_bytes = await generate_status_image(t_accs, is_online_mode=is_on)
        await m.answer_photo(photo=BufferedInputFile(file=img_bytes, filename="bss.png"))
        await msg.delete()
    except Exception as e: 
        logger.error(f"Ошибка img: {e}")
        await msg.edit_text(f"Ошибка: {e}")

@dp.message(Command("information"))
async def cmd_info(m: types.Message):
    log_act(m, "Команда /information")
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(chat_id=cid, message_id=status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Настройки", callback_data="ask_reset")]]))
    status_messages[cid] = msg.message_id
    await save_data()
    try: await bot.pin_chat_message(chat_id=m.chat.id, message_id=msg.message_id, disable_notification=True)
    except: pass

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    log_act(m, "Команда /list")
    if not notifications: return await m.answer("Список пуст.")
    res = "<b>📜 Настройки пингов:</b>\n"
    for acc, tags in notifications.items():
        res += f"• <code>{acc}</code>: {', '.join(tags)}\n"
    await m.answer(res, parse_mode="HTML")

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Формат: <code>/add Ник @тег</code>", parse_mode="HTML")
    acc, tag = args[1], args[2] if len(args) > 2 else (f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}")
    log_act(m, f"Добавление пинга {acc} {tag}")
    if acc not in notifications: notifications[acc] = []
    if tag not in notifications[acc]: 
        notifications[acc].append(tag)
        await save_data(); await m.answer(f"✅ Пинг добавлен для <b>{acc}</b>", parse_mode="HTML")
    else: await m.answer("Тег уже есть.")

@dp.message(Command("remove"))
async def cmd_remove(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Формат: <code>/remove Ник</code>", parse_mode="HTML")
    acc = args[1]
    log_act(m, f"Удаление пинга {acc}")
    if acc in notifications:
        del notifications[acc]; await save_data(); await m.answer(f"❌ {acc} удален.")
    else: await m.answer("Ник не найден.")

# --- АДМИНКА ---
@dp.message(Command("adm"))
async def cmd_adm(m: types.Message):
    if m.from_user.username != ALLOWED_ADMIN: return
    log_act(m, "Панель администратора")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_upd")],
        [InlineKeyboardButton(text="🧪 Тест вылета", callback_data="adm_test")]
    ])
    await m.answer("🕹 <b>Админ-панель:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "adm_upd")
async def adm_upd(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.edit_text("Отправьте сообщение для рассылки всем чатам:"); await state.set_state(PostCreation.waiting_for_content)

@dp.message(PostCreation.waiting_for_content)
async def broadcast_exec(m: types.Message, state: FSMContext):
    log_act(m, "Запуск рассылки")
    count = 0
    for cid in status_messages:
        try: await bot.send_message(chat_id=cid, text=f"📢 <b>ОБНОВЛЕНИЕ / НОВОСТИ:</b>\n\n{m.text}", parse_mode="HTML"); count += 1
        except: pass
    await m.answer(f"Успешно отправлено в {count} чатов."); await state.clear()

@dp.callback_query(F.data == "adm_test")
async def adm_test(cb: types.CallbackQuery):
    if not accounts: return await cb.answer("Никто не онлайн", show_alert=True)
    acc = list(accounts.keys())[0]
    accounts[acc] = time.time() - 300
    await cb.answer(f"Тест вылета запущен для {acc}"); await check_timeouts()

# --- ТЕХПЕРЕРЫВ ---
@dp.callback_query(F.data == "ask_reset")
async def tech_main(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 Тех. перерыв", callback_data="tp_menu")], [InlineKeyboardButton(text="⚠️ Сброс Сессии", callback_data="conf_res")], [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_only")]])
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data == "tp_menu")
async def tp_menu(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Создать", callback_data="tp_create"), InlineKeyboardButton(text="🗑 Удалить", callback_data="tp_delete")], [InlineKeyboardButton(text="🔙 Назад", callback_data="tp_back")]])
    await cb.message.edit_text("🛠 <b>Меню Техперерывов:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "tp_create")
async def tp_target(cb: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 ВСЕ", callback_data="target_all")], [InlineKeyboardButton(text="🐝 НИК", callback_data="target_one")]])
    await cb.message.edit_text("Для кого создать техперерыв?", reply_markup=kb); await state.set_state(TechPause.choosing_target)

@dp.callback_query(TechPause.choosing_target)
async def tp_target_choice(cb: types.CallbackQuery, state: FSMContext):
    log_act(cb, f"Выбор цели техперерыва: {cb.data}")
    if cb.data == "target_all":
        await state.update_data(target="ALL"); await cb.message.edit_text("Время (мин):"); await state.set_state(TechPause.entering_time)
    elif cb.data == "target_one":
        btns = [[InlineKeyboardButton(text=u, callback_data=f"sel_{u}")] for u in notifications]
        await cb.message.edit_text("Выберите ник:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    elif cb.data.startswith("sel_"):
        n = cb.data.replace("sel_", ""); await state.update_data(target=n)
        await cb.message.edit_text(f"Время для {n} (мин):"); await state.set_state(TechPause.entering_time)

@dp.message(TechPause.entering_time)
async def tp_time(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Введите число!")
    await state.update_data(mins=int(m.text))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Да", callback_data="auto_yes"), InlineKeyboardButton(text="❌ Нет", callback_data="auto_no")]])
    await m.answer("Автовыключение при входе?", reply_markup=kb); await state.set_state(TechPause.choosing_auto_off)

@dp.callback_query(TechPause.choosing_auto_off)
async def tp_final(cb: types.CallbackQuery, state: FSMContext):
    d = await state.get_data(); auto = cb.data == "auto_yes"
    log_act(cb, f"Финальная настройка паузы: {d['target']} на {d['mins']}м")
    t_list = list(notifications.keys()) if d['target'] == "ALL" else [d['target']]
    now = time.time()
    for t in t_list:
        until = max(now, pause_data.get(t, {}).get("until", now)) + (d['mins'] * 60)
        pause_data[t] = {"until": until, "auto_off": auto}; accounts.pop(t, None)
    await save_data(); await refresh_panels()
    msg = await cb.message.edit_text(f"🛠 <b>Пауза: {d['target']}</b> (+{d['mins']}м)"); await state.clear()
    await asyncio.sleep(10)
    try: await msg.delete()
    except: pass

@dp.callback_query(F.data == "tp_delete")
async def tp_del_menu(cb: types.CallbackQuery):
    if not pause_data: return await cb.answer("Нет активных пауз", show_alert=True)
    btns = [[InlineKeyboardButton(text=f"❌ {u}", callback_data=f"del_tp_{u}")] for u in pause_data]
    await cb.message.edit_text("Снять паузу:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("del_tp_"))
async def del_tp_exec(cb: types.CallbackQuery):
    acc = cb.data.replace("del_tp_", ""); pause_data.pop(acc, None)
    log_act(cb, f"Снятие паузы с {acc}"); await save_data(); await refresh_panels(); await tp_del_menu(cb)

# --- СИСТЕМА ---
async def check_timeouts():
    now = time.time()
    for u in list(pause_data.keys()):
        if now > pause_data[u]['until']: pause_data.pop(u, None)
    for u in list(accounts.keys()):
        if u not in pause_data and now - accounts[u] > 120:
            tags = " ".join(notifications.get(u, ["!"]))
            for cid in status_messages:
                try: await bot.send_message(cid, f"🚨 <b>{u}</b> ВЫЛЕТ!\n{tags}", parse_mode="HTML")
                except: pass
            accounts.pop(u, None); start_times.pop(u, None)
    await save_data(); await refresh_panels()

async def refresh_panels():
    txt = get_status_text(); kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Настройки", callback_data="ask_reset")]])
    for cid, mid in list(status_messages.items()):
        try: await bot.edit_message_text(txt, chat_id=cid, message_id=mid, parse_mode="HTML", reply_markup=kb)
        except: pass

async def handle_signal(request):
    try:
        d = await request.json(); u = d.get("username")
        if u:
            if u in pause_data and pause_data[u].get("auto_off"): pause_data.pop(u, None)
            if u not in start_times: start_times[u] = time.time()
            accounts[u] = time.time(); return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

@dp.callback_query(F.data == "refresh_only")
async def refresh_only(cb: types.CallbackQuery): await refresh_panels(); await cb.answer("Обновлено")

@dp.callback_query(F.data == "conf_res")
async def conf_res(cb: types.CallbackQuery):
    global session_restarts; session_restarts = 0; await save_data(); await refresh_panels(); await tp_back(cb)

@dp.callback_query(F.data == "tp_back")
async def tp_back(cb: types.CallbackQuery):
    await cb.message.edit_text(get_status_text(), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Настройки", callback_data="ask_reset")]]), parse_mode="HTML")

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
