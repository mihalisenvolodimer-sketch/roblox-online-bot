import os, asyncio, time, json, random, logging, sys, io, aiohttp, datetime
from datetime import timedelta, timezone
import redis.asyncio as redis
from PIL import Image, ImageDraw, ImageFont
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiohttp import web

# --- Конфигурация ---
VERSION = "V4.9.7-FULL"
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))
ALLOWED_ADMIN = "Gold_mod1" # Твой ник
FONT_PATH = "roboto_font.ttf"
FONT_URL = "https://cdn.jsdelivr.net/gh/googlefonts/roboto@main/src/hinted/Roboto-Bold.ttf"

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("BSS_PRO")

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Глобальные данные
accounts, start_times, notifications, status_messages = {}, {}, {}, {}
pause_data = {} 
raw_data_storage = {} # Сюда падают мёд/рюкзак для теста
total_restarts, session_restarts = 0, 0

BG_URLS = ["https://wallpaperaccess.com/full/7500647.png", "https://wallpaperaccess.com/full/14038208.jpg"]

class PostCreation(StatesGroup):
    waiting_for_title = State(); waiting_for_text = State(); waiting_for_photo = State(); confirming = State()

class TechPause(StatesGroup):
    choosing_target = State(); entering_time = State(); choosing_auto_off = State()

# --- Утилиты ---
def format_honey(n):
    if n is None: return "0"
    try:
        n = float(n)
        for unit in ['', 'K', 'M', 'B', 'T', 'Q']:
            if abs(n) < 1000.0: return f"{n:3.1f}{unit}"
            n /= 1000.0
        return f"{n:.1f}Q"
    except: return "0"

async def download_font():
    if not os.path.exists(FONT_PATH):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(FONT_URL) as r:
                    if r.status == 200:
                        with open(FONT_PATH, "wb") as f: f.write(await r.read())
        except: pass

async def get_roblox_avatar(username, session):
    try:
        async with session.post("https://users.roblox.com/v1/usernames/users", json={"usernames": [username], "excludeBannedUsers": False}) as resp:
            uid = (await resp.json())["data"][0]["id"]
        url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={uid}&size=150x150&format=Png&isCircular=true"
        async with session.get(url) as resp:
            img_url = (await resp.json())["data"][0]["imageUrl"]
        async with session.get(img_url) as resp:
            return Image.open(io.BytesIO(await resp.read())).convert("RGBA")
    except: return None

# --- База Данных ---
async def load_data():
    global db, notifications, status_messages, total_restarts, session_restarts, start_times, accounts, pause_data
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V49_STABLE")
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
    except: pass

async def save_data():
    if not db: return
    try:
        await db.set("BSS_V49_STABLE", json.dumps({
            "notifs": notifications, "msgs": status_messages, "total_restarts": total_restarts,
            "session_restarts": session_restarts, "starts": start_times, "accounts": accounts,
            "pause_data": pause_data
        }))
    except: pass

# --- Визуализация (Status Image) ---
async def generate_status_image(target_accounts, is_online_mode=True):
    width, row_h, head_h, foot_h = 750, 110, 130, 80
    height = head_h + (max(1, len(target_accounts)) * row_h) + foot_h
    img = Image.new("RGBA", (width, height), (40, 40, 40, 255))
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(random.choice(BG_URLS)) as r:
                bg = Image.open(io.BytesIO(await r.read())).convert("RGBA")
                bg = bg.resize((width, height), Image.LANCZOS)
                img.paste(bg, (0, 0))
    except: pass
    
    draw = ImageDraw.Draw(img)
    try: f_l = ImageFont.truetype(FONT_PATH, 42); f_m = ImageFont.truetype(FONT_PATH, 28); f_s = ImageFont.truetype(FONT_PATH, 18)
    except: f_l = f_m = f_s = ImageFont.load_default()
    
    draw.text((45, 40), "ОНЛАЙН СТАТУС" if is_online_mode else "ПЛАН МАКРОСА", font=f_l, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0,0,0))
    
    now = time.time()
    async with aiohttp.ClientSession() as session:
        for i, acc in enumerate(target_accounts):
            y = head_h + (i * row_h)
            draw.rounded_rectangle([30, y, width-30, y+row_h-10], fill=(0, 0, 0, 180), radius=15)
            av = await get_roblox_avatar(acc, session)
            if av: img.paste(av.resize((80, 80)), (45, y+10), av.resize((80, 80)))
            
            draw.text((140, y+20), acc, font=f_m, fill=(255, 255, 255))
            
            if acc in pause_data and now < pause_data[acc]['until']:
                draw.text((width-220, y+35), "ПАУЗА", font=f_m, fill=(255, 165, 0))
            elif is_online_mode and acc in accounts:
                dur = int(now - start_times.get(acc, now))
                draw.text((width-200, y+35), f"{dur//3600}ч {(dur%3600)//60}м", font=f_m, fill=(100, 255, 100))
            else:
                draw.text((width-210, y+35), "ОЖИДАНИЕ", font=f_m, fill=(180, 180, 180))
                    
    buf = io.BytesIO(); img.save(buf, format='PNG'); return buf.getvalue()

def get_status_text():
    now_str = datetime.datetime.now(timezone(timedelta(hours=2))).strftime("%H:%M:%S")
    now = time.time()
    text = f"<b>🐝 Улей BSS {VERSION}</b>\n🕒 Время: <b>{now_str}</b>\n\n"
    acc_list = sorted(list(set(list(accounts.keys()) + list(pause_data.keys()))))
    
    if not acc_list: text += "<blockquote>Сигналов нет...</blockquote>"
    else:
        for u in acc_list:
            if u in pause_data and now < pause_data[u]['until']:
                rem = int(pause_data[u]['until'] - now)
                text += f"🛠 <code>{u}</code> | <b>ТЕХПЕРЕРЫВ ({rem//60}м)</b>\n"
            elif u in accounts:
                d = int(now - start_times.get(u, now))
                text += f"🟢 <code>{u}</code> | 🕒 <b>{d//3600}ч {(d%3600)//60}м</b>\n"
    return text

# --- ХЕНДЛЕРЫ КОМАНД ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer(f"<b>🐝 BSS {VERSION}</b>\n\n/information — Статус\n/img — Картинка\n/list — Пинги\n/add [Ник] [Тег]\n/honeyupdtest — Тест статы", parse_mode="HTML")

@dp.message(Command("honeyupdtest"))
async def cmd_honey_test(m: types.Message):
    if not raw_data_storage: return await m.answer("❌ Данных нет. Запусти скрипт!")
    res = "🧪 <b>РЕАЛЬНЫЕ ДАННЫЕ (ТЕСТ):</b>\n\n"
    for user, data in raw_data_storage.items():
        h, p, c = data.get("honey", 0), data.get("pollen", 0), data.get("capacity", 1)
        res += f"👤 <code>{user}</code>\n🍯 Мёд: {format_honey(h)}\n🎒 Рюкзак: {int((p/c)*100)}% ({p}/{c})\n\n"
    await m.answer(res, parse_mode="HTML")

@dp.message(Command("information"))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(chat_id=cid, message_id=status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ Настройки", callback_data="ask_reset")]]))
    status_messages[cid] = msg.message_id
    await save_data()

@dp.message(Command("img"))
async def cmd_img(m: types.Message):
    args = m.text.split()[1:]
    is_on = len(args) == 0
    t_accs = list(set(list(accounts.keys()) + list(pause_data.keys()))) if is_on else args
    if not t_accs: return await m.answer("Список пуст.")
    
    msg = await m.answer("🎨 Рисую...")
    try:
        img_bytes = await generate_status_image(t_accs, is_online_mode=is_on)
        await m.answer_photo(photo=BufferedInputFile(file=img_bytes, filename="status.png"))
        await msg.delete()
    except Exception as e: await msg.edit_text(f"Ошибка: {e}")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Список пуст.")
    res = "<b>📜 Настройка уведомлений:</b>\n"
    for acc, tags in notifications.items(): res += f"• <code>{acc}</code>: {', '.join(tags)}\n"
    await m.answer(res, parse_mode="HTML")

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Формат: /add Ник @тег")
    acc, tag = args[1], args[2] if len(args) > 2 else f"ID:{m.from_user.id}"
    if acc not in notifications: notifications[acc] = []
    notifications[acc].append(tag); await save_data()
    await m.answer(f"✅ Аккаунт {acc} добавлен для уведомлений.")

@dp.message(Command("remove"))
async def cmd_remove(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Укажи ник.")
    if args[1] in notifications:
        del notifications[args[1]]; await save_data()
        await m.answer(f"❌ Аккаунт {args[1]} удален.")

# --- АДМИНКА И РАССЫЛКА ---
@dp.message(Command("adm"))
async def cmd_adm(m: types.Message):
    if m.from_user.username != ALLOWED_ADMIN: return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Рассылка всем", callback_data="adm_broadcast")]])
    await m.answer("🕹 <b>Админ-панель:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "adm_broadcast")
async def adm_bc(cb: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📝 С заголовком", callback_data="bc_t_yes"), InlineKeyboardButton(text="💬 Без", callback_data="bc_t_no")]])
    await cb.message.edit_text("Выбери формат:", reply_markup=kb)

@dp.callback_query(F.data.startswith("bc_t_"))
async def bc_step1(cb: types.CallbackQuery, state: FSMContext):
    use_t = cb.data == "bc_t_yes"
    await state.update_data(has_title=use_t)
    if use_t:
        await cb.message.edit_text("Введи заголовок:"); await state.set_state(PostCreation.waiting_for_title)
    else:
        await cb.message.edit_text("Введи текст сообщения:"); await state.set_state(PostCreation.waiting_for_text)

@dp.message(PostCreation.waiting_for_title)
async def bc_title(m: types.Message, state: FSMContext):
    await state.update_data(title=m.text); await m.answer("Теперь введи текст сообщения:"); await state.set_state(PostCreation.waiting_for_text)

@dp.message(PostCreation.waiting_for_text)
async def bc_text(m: types.Message, state: FSMContext):
    await state.update_data(text=m.text); await m.answer("Пришли фото или нажми пропустить:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить ⏩", callback_data="bc_skip_photo")]]))
    await state.set_state(PostCreation.waiting_for_photo)

@dp.message(PostCreation.waiting_for_photo, F.photo)
async def bc_photo(m: types.Message, state: FSMContext):
    await state.update_data(photo_id=m.photo[-1].file_id); await show_bc_preview(m, state)

@dp.callback_query(F.data == "bc_skip_photo")
async def bc_skip_p(cb: types.CallbackQuery, state: FSMContext):
    await state.update_data(photo_id=None); await show_bc_preview(cb.message, state)

async def show_bc_preview(m, state):
    d = await state.get_data(); text = f"<blockquote>{d['text']}</blockquote>"
    if d.get("has_title"): text = f"📢 <b>{d['title']}</b>\n\n{text}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ ОТПРАВИТЬ", callback_data="bc_confirm"), InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="bc_cancel")]])
    if d.get("photo_id"): await bot.send_photo(m.chat.id, d["photo_id"], caption=text, parse_mode="HTML", reply_markup=kb)
    else: await bot.send_message(m.chat.id, text, parse_mode="HTML", reply_markup=kb)
    await state.set_state(PostCreation.confirming)

@dp.callback_query(F.data == "bc_confirm")
async def bc_send(cb: types.CallbackQuery, state: FSMContext):
    d = await state.get_data(); text = f"<blockquote>{d['text']}</blockquote>"
    if d.get("has_title"): text = f"📢 <b>{d['title']}</b>\n\n{text}"
    count = 0
    for cid in status_messages:
        try:
            if d.get("photo_id"): await bot.send_photo(cid, d["photo_id"], caption=text, parse_mode="HTML")
            else: await bot.send_message(cid, text, parse_mode="HTML")
            count += 1
        except: pass
    await cb.message.answer(f"✅ Рассылка завершена! Отправлено в {count} чатов."); await state.clear()

# --- ТЕХНИЧЕСКИЕ ПАУЗЫ ---
@dp.callback_query(F.data == "ask_reset")
async def tech_root(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 Тех. перерыв", callback_data="tp_menu")], [InlineKeyboardButton(text="🔄 Обновить панель", callback_data="refresh_only")]])
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data == "tp_menu")
async def tp_menu(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Создать", callback_data="tp_add"), InlineKeyboardButton(text="🗑 Удалить все", callback_data="tp_clear_all")], [InlineKeyboardButton(text="⬅️ Назад", callback_data="ask_reset")]])
    await cb.message.edit_text("🛠 <b>Управление паузами:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "tp_add")
async def tp_add_start(cb: types.CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="ВСЕ", callback_data="target_all")]]
    for acc in notifications: kb.append([InlineKeyboardButton(text=acc, callback_data=f"target_{acc}")])
    await cb.message.edit_text("Выбери аккаунт:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(TechPause.choosing_target)

@dp.callback_query(F.data.startswith("target_"), TechPause.choosing_target)
async def tp_target(cb: types.CallbackQuery, state: FSMContext):
    target = cb.data.replace("target_", ""); await state.update_data(target=target)
    await cb.message.edit_text(f"На сколько минут поставить <b>{target}</b>?", parse_mode="HTML")
    await state.set_state(TechPause.entering_time)

@dp.message(TechPause.entering_time)
async def tp_time(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Введи число!")
    mins = int(m.text); d = await state.get_data(); now = time.time()
    targets = list(notifications.keys()) if d['target'] == "all" else [d['target']]
    for t in targets: pause_data[t] = {"until": now + mins * 60, "auto_off": True}
    await save_data(); await m.answer(f"✅ Пауза на {mins}м установлена."); await state.clear(); await refresh_panels()

@dp.callback_query(F.data == "tp_clear_all")
async def tp_clear(cb: types.CallbackQuery):
    pause_data.clear(); await save_data(); await cb.answer("Паузы сброшены"); await refresh_panels()

# --- СЕРВЕР И МОНИТОРИНГ ---
async def handle_signal(request):
    try:
        d = await request.json(); u = d.get("username")
        if u:
            if u in pause_data and pause_data[u].get("auto_off"): pause_data.pop(u, None)
            if u not in start_times: start_times[u] = time.time()
            accounts[u] = time.time()
            # ТЕСТОВАЯ ЗАПИСЬ РЕАЛЬНЫХ ДАННЫХ
            raw_data_storage[u] = {"honey": d.get("honey", 0), "pollen": d.get("pollen", 0), "capacity": d.get("capacity", 1)}
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def check_timeouts():
    now = time.time()
    for u in list(accounts.keys()):
        if now - accounts[u] > 120:
            tags = " ".join(notifications.get(u, ["!"]))
            for cid in status_messages:
                try: await bot.send_message(cid, f"🚨 <b>{u}</b> ВЫЛЕТ!\n{tags}", parse_mode="HTML")
                except: pass
            accounts.pop(u, None); start_times.pop(u, None)
    await save_data(); await refresh_panels()

async def refresh_panels():
    txt = get_status_text(); kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ Настройки", callback_data="ask_reset")]])
    for cid, mid in list(status_messages.items()):
        try: await bot.edit_message_text(txt, chat_id=cid, message_id=mid, parse_mode="HTML", reply_markup=kb)
        except: pass

async def monitor_loop():
    while True:
        try: await check_timeouts()
        except: pass
        await asyncio.sleep(30)

async def main():
    await download_font(); await load_data()
    asyncio.create_task(monitor_loop())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await bot.delete_webhook(drop_pending_updates=True); await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
