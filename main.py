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
VERSION = "V4.0"
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
pause_data = {} # {username: {"until": timestamp, "auto_off": bool}}
total_restarts, session_restarts = 0, 0

QUOTES = ["Пчёлы не спят, они фармят!", "Мёд сам себя не соберёт.", "Удачного фарма, легенда!", "Мониторинг на страже."]
BG_URLS = ["https://wallpaperaccess.com/full/7500647.png", "https://wallpaperaccess.com/full/14038149.jpg"]

class PostCreation(StatesGroup):
    waiting_for_content, waiting_for_title, waiting_for_desc, waiting_for_confirm = State(), State(), State(), State()

class TechPause(StatesGroup):
    choosing_target, entering_time, choosing_auto_off = State(), State(), State()

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
            uid = data["data"][0]["id"]
        url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={uid}&size=150x150&format=Png&isCircular=true"
        async with session.get(url) as resp:
            img_url = (await resp.json())["data"][0]["imageUrl"]
        async with session.get(img_url) as resp:
            return Image.open(io.BytesIO(await resp.read())).convert("RGBA")
    except: return None

# --- Работа с БД (Вернули старый ключ) ---
async def load_data():
    global db, notifications, status_messages, total_restarts, session_restarts, start_times, accounts, pause_data
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        # ИСПОЛЬЗУЕМ СТАРЫЙ КЛЮЧ, ЧТОБЫ НЕ СЛЕТЕЛИ ДАННЫЕ
        raw = await db.get("BSS_V37_STABLE_FINAL")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            total_restarts = data.get("total_restarts", 0) + 1
            session_restarts = data.get("session_restarts", 0) + 1
            # Аккуратно подтягиваем паузы, если их не было - будет пусто
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
        # СОХРАНЯЕМ В СТАРЫЙ КЛЮЧ + НОВЫЙ ПАРАМЕТР pause_data
        await db.set("BSS_V37_STABLE_FINAL", json.dumps({
            "notifs": notifications, "msgs": status_messages, "total_restarts": total_restarts,
            "session_restarts": session_restarts, "starts": start_times, "accounts": accounts,
            "pause_data": pause_data
        }))
    except: pass

# --- Основная логика текста ---
def get_status_text():
    tz = timezone(timedelta(hours=2))
    now_str = datetime.datetime.now(tz).strftime("%H:%M:%S")
    now = time.time()
    text = f"<b>🐝 Улей BSS {VERSION}</b>\n🕒 GMT+2: <b>{now_str}</b>\n🔄 Сессия: {session_restarts}\n\n"
    
    acc_list = sorted(list(set(list(accounts.keys()) + list(pause_data.keys()))))
    
    if not acc_list:
        text += "<blockquote>Ожидание сигналов...</blockquote>"
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

# --- Команды ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer(f"<b>🐝 BSS Monitor {VERSION}</b>\n\nВсего запусков: {total_restarts}\nСессия: {session_restarts}\n\n/information — Панель\n/adm — Админ-пульт", parse_mode="HTML")

@dp.message(Command("adm"))
async def cmd_adm(m: types.Message):
    if m.from_user.username != ALLOWED_ADMIN: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка Новостей", callback_data="adm_upd")],
        [InlineKeyboardButton(text="🧪 Тест Вылета", callback_data="adm_test")],
        [InlineKeyboardButton(text="📋 Список Пингов", callback_data="adm_list")]
    ])
    await m.answer("🕹 <b>Панель администратора:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "adm_test")
async def adm_test_list(cb: types.CallbackQuery):
    if not accounts: return await cb.answer("Никто не онлайн", show_alert=True)
    btns = [[InlineKeyboardButton(text=f"Выбить {u}", callback_data=f"do_test_{u}")] for u in accounts]
    btns.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_adm")])
    await cb.message.edit_text("Выберите аккаунт для теста вылета:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("do_test_"))
async def do_test(cb: types.CallbackQuery):
    user = cb.data.replace("do_test_", "")
    if user in accounts:
        accounts[user] = time.time() - 300
        await cb.answer(f"Тест запущен для {user}")
        await check_timeouts()
    await adm_test_list(cb)

@dp.callback_query(F.data == "adm_list")
async def adm_list(cb: types.CallbackQuery):
    if not notifications: return await cb.answer("Список пуст.", show_alert=True)
    res = "<b>📜 Настройки пингов:</b>\n"
    for acc, tags in notifications.items():
        status = " (🛠 ПАУЗА)" if acc in pause_data and time.time() < pause_data[acc]['until'] else ""
        res += f"• <code>{acc}</code>: {', '.join(tags)}{status}\n"
    await cb.message.edit_text(res, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_adm")]]), parse_mode="HTML")

@dp.callback_query(F.data == "back_to_adm")
async def back_to_adm(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка Новостей", callback_data="adm_upd")],
        [InlineKeyboardButton(text="🧪 Тест Вылета", callback_data="adm_test")],
        [InlineKeyboardButton(text="📋 Список Пингов", callback_data="adm_list")]
    ])
    await cb.message.edit_text("🕹 <b>Панель администратора:</b>", reply_markup=kb, parse_mode="HTML")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Список пуст.")
    res = "<b>📜 Настройки пингов:</b>\n"
    for acc, tags in notifications.items():
        status = " (🛠 ПАУЗА)" if acc in pause_data and time.time() < pause_data[acc]['until'] else ""
        res += f"• <code>{acc}</code>: {', '.join(tags)}{status}\n"
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

# --- ЛОГИКА ТЕХПЕРЕРЫВА ---
@dp.callback_query(F.data == "ask_reset")
async def tech_main(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Тех. перерыв", callback_data="tp_menu")],
        [InlineKeyboardButton(text="⚠️ Сброс Сессии", callback_data="conf_res")],
        [InlineKeyboardButton(text="🔄 Обновить время", callback_data="refresh_only")]
    ])
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data == "tp_menu")
async def tp_menu(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать", callback_data="tp_create"), InlineKeyboardButton(text="🗑 Удалить", callback_data="tp_delete")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="tp_back")]
    ])
    await cb.message.edit_text("🛠 <b>Меню Техперерывов:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "tp_create")
async def tp_target(cb: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Весь Улей (ВСЕ)", callback_data="target_all")],
        [InlineKeyboardButton(text="🐝 Конкретный ник", callback_data="target_one")]
    ])
    await cb.message.edit_text("Для кого создать техперерыв?", reply_markup=kb)
    await state.set_state(TechPause.choosing_target)

@dp.callback_query(TechPause.choosing_target)
async def tp_target_choice(cb: types.CallbackQuery, state: FSMContext):
    if cb.data == "target_all":
        await state.update_data(target="ALL")
        await cb.message.edit_text("Введите время перерыва (в минутах):")
        await state.set_state(TechPause.entering_time)
    else:
        # Показываем кнопки из списка уведомлений
        btns = [[InlineKeyboardButton(text=u, callback_data=f"sel_{u}")] for u in notifications]
        await cb.message.edit_text("Выберите ник:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("sel_"))
async def tp_sel_one(cb: types.CallbackQuery, state: FSMContext):
    await state.update_data(target=cb.data.replace("sel_", ""))
    await cb.message.edit_text("Введите время перерыва (в минутах):")
    await state.set_state(TechPause.entering_time)

@dp.message(TechPause.entering_time)
async def tp_time(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Введите число!")
    await state.update_data(mins=int(m.text))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="auto_yes"), InlineKeyboardButton(text="❌ Нет", callback_data="auto_no")]
    ])
    await m.answer("Завершать автоматически при входе макроса?", reply_markup=kb)
    await state.set_state(TechPause.choosing_auto_off)

@dp.callback_query(TechPause.choosing_auto_off)
async def tp_final(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    auto = cb.data == "auto_yes"
    now = time.time()
    dur = data['mins'] * 60
    
    target_list = list(notifications.keys()) if data['target'] == "ALL" else [data['target']]
    
    for t in target_list:
        current_until = pause_data.get(t, {}).get("until", now)
        base = max(now, current_until)
        pause_data[t] = {"until": base + dur, "auto_off": auto}
        accounts.pop(t, None) # Убираем из активных
    
    await save_data(); await refresh_panels()
    
    msg = await cb.message.edit_text(
        f"🛠 <b>Техперерыв активирован!</b>\nЦель: {data['target']}\nВремя: +{data['mins']} мин.\nАвтозавершение: {'Да' if auto else 'Нет'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ ОТМЕНА", callback_data=f"abort_tp_{data['target']}")]])
    )
    await state.clear()
    await asyncio.sleep(30)
    try: await msg.delete()
    except: pass

@dp.callback_query(F.data.startswith("abort_tp_"))
async def abort_tp(cb: types.CallbackQuery):
    target = cb.data.replace("abort_tp_", "")
    if target == "ALL": pause_data.clear()
    else: pause_data.pop(target, None)
    await save_data(); await refresh_panels()
    await cb.answer("Техперерыв отменен!"); await cb.message.delete()

@dp.callback_query(F.data == "tp_delete")
async def tp_delete_menu(cb: types.CallbackQuery):
    if not pause_data: return await cb.answer("Нет активных перерывов", show_alert=True)
    btns = [[InlineKeyboardButton(text=f"Удалить {u}", callback_data=f"del_tp_{u}")] for u in pause_data]
    btns.append([InlineKeyboardButton(text="🔙 Назад", callback_data="tp_back")])
    await cb.message.edit_text("Выберите перерыв для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("del_tp_"))
async def del_tp_exec(cb: types.CallbackQuery):
    u = cb.data.replace("del_tp_", "")
    pause_data.pop(u, None)
    await save_data(); await refresh_panels(); await tp_delete_menu(cb)

# --- ЯДРО МОНИТОРИНГА ---
async def check_timeouts():
    now = time.time()
    # 1. Проверка завершения пауз
    for u in list(pause_data.keys()):
        if now > pause_data[u]['until']:
            pause_data.pop(u, None) # Тихое завершение
            
    # 2. Проверка таймаутов
    for u in list(accounts.keys()):
        if u in pause_data: continue # Игнорим тех, кто на паузе
        if now - accounts[u] > 120:
            tags = " ".join(notifications.get(u, ["(без пинга)"]))
            for cid in status_messages:
                try: await bot.send_message(cid, f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{tags}", parse_mode="HTML")
                except: pass
            accounts.pop(u, None); start_times.pop(u, None)
    
    await save_data(); await refresh_panels()

async def refresh_panels():
    txt = get_status_text()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Настройки / Рестарт", callback_data="ask_reset")]])
    for cid, mid in list(status_messages.items()):
        try: await bot.edit_message_text(txt, chat_id=cid, message_id=mid, parse_mode="HTML", reply_markup=kb)
        except: pass

async def handle_signal(request):
    try:
        d = await request.json(); u = d.get("username")
        if u:
            now = time.time()
            # Автозавершение паузы при сигнале
            if u in pause_data and pause_data[u].get("auto_off"):
                pause_data.pop(u, None)
            
            if u not in start_times: start_times[u] = now
            accounts[u] = now; return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

# --- Стандартные обработчики ---
@dp.callback_query(F.data == "adm_upd")
async def adm_upd_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_data({"photos": []})
    await cb.message.answer("Тип новости:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="С заголовком", callback_data="u_t"), InlineKeyboardButton(text="Без", callback_data="u_s")]]))

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

@dp.message(Command("information"))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(chat_id=cid, message_id=status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Настройки / Рестарт", callback_data="ask_reset")]]))
    status_messages[cid] = msg.message_id
    try: await bot.pin_chat_message(chat_id=cid, message_id=msg.message_id, disable_notification=True)
    except: pass
    await save_data()

# Заглушки для колбэков навигации
@dp.callback_query(F.data == "tp_back")
async def tp_back(cb: types.CallbackQuery):
    await cb.message.edit_text(get_status_text(), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Настройки / Рестарт", callback_data="ask_reset")]]), parse_mode="HTML")

@dp.callback_query(F.data == "refresh_only")
async def refresh_only(cb: types.CallbackQuery): await refresh_panels(); await cb.answer("Обновлено")

@dp.callback_query(F.data == "conf_res")
async def conf_res_v4(cb: types.CallbackQuery):
    global session_restarts; session_restarts = 0; await save_data(); await cb.answer("Сессия сброшена!"); await refresh_panels(); await tp_back(cb)

# --- Запуск ---
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
