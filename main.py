import os, asyncio, time, json, random, logging, sys, io, aiohttp, datetime
from datetime import timedelta, timezone
import redis.asyncio as redis
from PIL import Image, ImageDraw, ImageFont
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    BufferedInputFile, 
    InlineQuery, 
    InlineQueryResultArticle, 
    InputTextMessageContent
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiohttp import web

# --- Настройки ---
VERSION = "V6.0"
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))
ALLOWED_ADMIN = "Gold_mod1" 
DB_KEY = "BSS_GLOBAL_DATABASE_PRO" 
FONT_PATH = "roboto_font.ttf"
FONT_URL = "https://cdn.jsdelivr.net/gh/googlefonts/roboto@main/src/hinted/Roboto-Bold.ttf"

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("BSS_PRO")

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Глобальные данные
accounts, start_times, notifications, status_messages = {}, {}, {}, {}
pause_data, acc_stats = {}, {}
initial_honey, action_logs = {}, []
total_restarts, session_restarts = 0, 0

# НОВЫЕ ПЕРЕМЕННЫЕ АДМИНКИ
disconnect_counts = {}
check_timeout = 120
custom_backgrounds = []
active_bg = None

BG_URLS = ["https://wallpaperaccess.com/full/7500647.png", "https://wallpaperaccess.com/full/14038208.jpg"]

class PostCreation(StatesGroup): waiting_for_title = State(); waiting_for_text = State(); waiting_for_photo = State(); confirming = State()
class TechPause(StatesGroup): choosing_target = State(); entering_time = State(); choosing_mode = State()
class AdminConfig(StatesGroup): waiting_for_bg = State()

# --- Вспомогательные функции ---
def format_honey(n):
    if n is None: return "0"
    try:
        n = float(n)
        for unit in ['', 'K', 'M', 'B', 'T', 'Q']:
            if abs(n) < 1000.0: return f"{n:3.1f}{unit}"
            n /= 1000.0
        return f"{n:.1f}Q"
    except: return "0"

def add_log(text):
    now = datetime.datetime.now(timezone(timedelta(hours=2))).strftime("%H:%M:%S")
    action_logs.insert(0, f"🕒 <code>{now}</code> — {text}")
    if len(action_logs) > 10: action_logs.pop()

async def download_font():
    if not os.path.exists(FONT_PATH):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(FONT_URL) as r:
                    if r.status == 200:
                        with open(FONT_PATH, "wb") as f: f.write(await r.read())
        except: pass

async def get_avatar(username, session):
    try:
        async with session.post("https://users.roblox.com/v1/usernames/users", json={"usernames": [username], "excludeBannedUsers": False}) as resp:
            uid = (await resp.json())["data"][0]["id"]
        url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={uid}&size=150x150&format=Png&isCircular=true"
        async with session.get(url) as resp: img_url = (await resp.json())["data"][0]["imageUrl"]
        async with session.get(img_url) as resp: return Image.open(io.BytesIO(await resp.read())).convert("RGBA")
    except: return None

# --- База Данных ---
async def load_data():
    global db, notifications, status_messages, total_restarts, session_restarts, start_times, accounts, pause_data, action_logs, initial_honey
    global disconnect_counts, check_timeout, custom_backgrounds, active_bg
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get(DB_KEY)
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            total_restarts = data.get("total_restarts", 0) + 1
            session_restarts = data.get("session_restarts", 0) + 1
            pause_data = data.get("pause_data", {})
            action_logs = data.get("logs", [])
            initial_honey = data.get("init_h", {})
            disconnect_counts = data.get("dc_counts", {})
            check_timeout = data.get("check_timeout", 120)
            custom_backgrounds = data.get("custom_bgs", [])
            active_bg = data.get("active_bg", None)
            saved_accs = data.get("accounts", {})
            now = time.time()
            for u, p in saved_accs.items():
                if now - float(p) < check_timeout:
                    accounts[u] = float(p)
                    if u in data.get("starts", {}): start_times[u] = float(data["starts"][u])
    except: pass

async def save_data():
    if not db: return
    try:
        await db.set(DB_KEY, json.dumps({
            "notifs": notifications, "msgs": status_messages, "total_restarts": total_restarts,
            "session_restarts": session_restarts, "starts": start_times, "accounts": accounts,
            "pause_data": pause_data, "logs": action_logs, "init_h": initial_honey,
            "dc_counts": disconnect_counts, "check_timeout": check_timeout,
            "custom_bgs": custom_backgrounds, "active_bg": active_bg
        }))
    except: pass

# --- Отрисовка ---
async def generate_status_image(target_accounts, is_online_mode=True):
    width, row_h, head_h, foot_h = 750, 115, 130, 80
    height = head_h + (max(1, len(target_accounts)) * row_h) + foot_h
    img = Image.new("RGBA", (width, height), (40, 40, 40, 255))
    try:
        if active_bg:
            file = await bot.get_file(active_bg)
            bg_bytes = io.BytesIO()
            await bot.download_file(file.file_path, destination=bg_bytes)
            bg = Image.open(bg_bytes).convert("RGBA")
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(random.choice(BG_URLS)) as r:
                    bg = Image.open(io.BytesIO(await r.read())).convert("RGBA")
        bg = bg.resize((width, height), Image.LANCZOS)
        img.paste(bg, (0, 0))
    except Exception as e: pass
    draw = ImageDraw.Draw(img)
    try: f_l = ImageFont.truetype(FONT_PATH, 42); f_m = ImageFont.truetype(FONT_PATH, 28); f_s = ImageFont.truetype(FONT_PATH, 18)
    except: f_l = f_m = f_s = ImageFont.load_default()
    draw.text((45, 40), "ОНЛАЙН МОНИТОРИНГ", font=f_l, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0,0,0))
    now = time.time()
    async with aiohttp.ClientSession() as session:
        for i, acc in enumerate(target_accounts):
            y = head_h + (i * row_h)
            draw.rounded_rectangle([30, y, width-30, y+row_h-10], fill=(0, 0, 0, 180), radius=15)
            av = await get_avatar(acc, session)
            if av: img.paste(av.resize((85, 85)), (45, y+10), av.resize((85, 85)))
            
            draw.text((145, y+12), acc, font=f_m, fill=(255, 255, 255))
            st = acc_stats.get(acc, {"h": "0", "b": "0%", "raw_b": 0, "prof": "0"})
            draw.text((145, y+50), f"Honey: {st['h']} (+{st['prof']})", font=f_s, fill=(200, 200, 200))
            draw.text((145, y+75), " Bag:", font=f_s, fill=(200, 200, 200))
            bar_x, bar_y, bar_w, bar_h = 220, y+78, 150, 14
            draw.rounded_rectangle([bar_x, bar_y, bar_x+bar_w, bar_y+bar_h], fill=(80, 80, 80, 255), radius=5)
            pct = min(100, st['raw_b'])
            if pct > 0:
                fill_w = int((pct / 100) * bar_w)
                color = (50, 205, 50) if pct < 60 else ((255, 165, 0) if pct < 85 else (255, 69, 0))
                draw.rounded_rectangle([bar_x, bar_y, bar_x+fill_w, bar_y+bar_h], fill=color, radius=5)
            draw.text((bar_x+bar_w+10, y+75), st['b'], font=f_s, fill=(255, 255, 255))
            if acc in pause_data and now < pause_data[acc]['until']:
                draw.text((width-220, y+35), "ПАУЗА", font=f_m, fill=(255, 165, 0))
            elif is_online_mode and acc in accounts:
                dur = int(now - start_times.get(acc, now))
                draw.text((width-200, y+35), f"{dur//3600}ч {(dur%3600)//60}м", font=f_m, fill=(100, 255, 100))
            else:
                draw.text((width-210, y+35), "WAITING", font=f_m, fill=(180, 180, 180))
    buf = io.BytesIO(); img.save(buf, format='PNG'); return buf.getvalue()

def get_status_text():
    now_str = datetime.datetime.now(timezone(timedelta(hours=2))).strftime("%H:%M:%S")
    now = time.time()
    text = f"<b>🐝 Улей BSS {VERSION}</b>\n🕒 Время: <b>{now_str}</b>\n"
    text += f"🔄 Рестартов: <b>{session_restarts}</b> (Всего: {total_restarts})\n\n"
    acc_list = sorted(list(set(list(accounts.keys()) + list(pause_data.keys()))))
    if not acc_list: text += "<blockquote>Ожидание сигналов...</blockquote>"
    else:
        for u in acc_list:
            if u in pause_data and now < pause_data[u]['until']:
                rem = int(pause_data[u]['until'] - now)
                mode = "A" if pause_data[u].get("auto_off") else "H"
                text += f"🛠 <code>{u}</code> | <b>ПАУЗА ({mode}) {rem//60}м</b>\n"
            elif u in accounts:
                d = int(now - start_times.get(u, now))
                st = acc_stats.get(u, {"h": "0", "b": "0%", "prof": "0"})
                text += f"🟢 <code>{u}</code> | 🍯 <b>{st['h']} (+{st['prof']})</b> | 🎒 <b>{st['b']}</b>\n"
                text += f"└ 🕒 <b>{d//3600}ч {(d%3600)//60}м в сети</b>\n\n"
    return text

# --- Хендлеры Юзера ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer(f"<b>🐝 BSS {VERSION}</b>\n🔄 Общих рестартов бота: <b>{total_restarts}</b>\n\n/information — Статус\n/img — Картинка\n/logs — Логи событий\n/list — Пинги\n/add [Ник] [Тег]", parse_mode="HTML")

@dp.message(Command("logs"))
async def cmd_logs(m: types.Message):
    if not action_logs: return await m.answer("Список логов пока пуст.")
    await m.answer("<b>📋 Последние 10 событий:</b>\n\n" + "\n".join(action_logs), parse_mode="HTML")

@dp.message(Command("information"))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    if cid in status_messages:
        try: await bot.delete_message(chat_id=cid, message_id=status_messages[cid])
        except: pass
    msg = await m.answer(get_status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ Настройки", callback_data="ask_reset")]]))
    status_messages[cid] = msg.message_id
    try: await bot.pin_chat_message(chat_id=m.chat.id, message_id=msg.message_id, disable_notification=True)
    except: pass
    await save_data()

@dp.message(Command("img"))
async def cmd_img(m: types.Message):
    args = m.text.split()[1:]; is_on = len(args) == 0
    t_accs = list(set(list(accounts.keys()) + list(pause_data.keys()))) if is_on else args
    if not t_accs: return await m.answer("Список пуст.")
    msg = await m.answer("🎨 Рисую..."); img_bytes = await generate_status_image(t_accs, is_online_mode=is_on)
    await m.answer_photo(photo=BufferedInputFile(file=img_bytes, filename="bss.png")); await msg.delete()

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Список пуст.")
    res = "<b>📜 Список уведомлений (Пинги):</b>\n"
    for acc, tags in notifications.items(): res += f"• <code>{acc}</code>: {', '.join(tags)}\n"
    await m.answer(res, parse_mode="HTML")

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split(); acc = args[1] if len(args) > 1 else None
    if not acc: return await m.answer("Формат: /add Ник @тег")
    tag = args[2] if len(args) > 2 else (f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}")
    if acc not in notifications: notifications[acc] = []
    if tag not in notifications[acc]: notifications[acc].append(tag)
    await save_data(); await m.answer(f"✅ <b>{acc}</b> добавлен.", parse_mode="HTML")

@dp.message(Command("remove"))
async def cmd_remove(m: types.Message):
    args = m.text.split(); acc = args[1] if len(args) > 1 else None
    if acc in notifications: del notifications[acc]; await save_data(); await m.answer(f"❌ {acc} удален.")

# --- Админка Основная ---
@dp.message(Command("adm"))
async def cmd_adm(m: types.Message):
    if m.from_user.username != ALLOWED_ADMIN: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast"), InlineKeyboardButton(text="🧪 Тест вылета", callback_data="adm_test_dc_menu")],
        [InlineKeyboardButton(text="📊 Итоги сессии", callback_data="adm_stats"), InlineKeyboardButton(text="🎚 Чувствительность", callback_data="adm_timeout")],
        [InlineKeyboardButton(text="🖼 Управление фонами", callback_data="adm_bg_menu")]
    ])
    await m.answer("🕹 <b>Панель администратора:</b>", reply_markup=kb, parse_mode="HTML")

# --- Новые функции админки ---
@dp.callback_query(F.data == "adm_stats")
async def cb_adm_stats(cb: types.CallbackQuery):
    total_prof = sum(st.get("raw_prof", 0) for st in acc_stats.values())
    top_farmer = max(acc_stats.keys(), key=lambda u: acc_stats[u].get("raw_prof", 0), default="Нет данных") if acc_stats else "Нет данных"
    top_prof = format_honey(acc_stats[top_farmer].get("raw_prof", 0)) if top_farmer in acc_stats else "0"
    sleepyhead = max(disconnect_counts.keys(), key=lambda u: disconnect_counts[u], default="Нет данных") if disconnect_counts else "Нет вылетов"
    sl_cnt = disconnect_counts[sleepyhead] if sleepyhead in disconnect_counts else 0
    text = f"<b>📊 Итоги текущей сессии:</b>\n\n"
    text += f"🍯 <b>Общий фарм:</b> {format_honey(total_prof)}\n"
    text += f"👑 <b>Топ фармер:</b> {top_farmer} (+{top_prof})\n"
    text += f"💤 <b>Соня дня:</b> {sleepyhead} ({sl_cnt} вылетов)\n"
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]]))

@dp.callback_query(F.data == "adm_back")
async def cb_adm_back(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast"), InlineKeyboardButton(text="🧪 Тест вылета", callback_data="adm_test_dc_menu")],
        [InlineKeyboardButton(text="📊 Итоги сессии", callback_data="adm_stats"), InlineKeyboardButton(text="🎚 Чувствительность", callback_data="adm_timeout")],
        [InlineKeyboardButton(text="🖼 Управление фонами", callback_data="adm_bg_menu")]
    ])
    await cb.message.edit_text("🕹 <b>Панель администратора:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("adm_timeout"))
async def cb_adm_timeout(cb: types.CallbackQuery):
    global check_timeout
    if "plus" in cb.data: check_timeout += 30
    elif "minus" in cb.data and check_timeout > 30: check_timeout -= 30
    await save_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➖ 30 сек", callback_data="adm_timeout_minus"), InlineKeyboardButton(text="➕ 30 сек", callback_data="adm_timeout_plus")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
    ])
    await cb.message.edit_text(f"🎚 <b>Чувствительность вылета:</b>\n\nТекущий таймаут: <b>{check_timeout} сек.</b>\n(Если аккаунт не шлет сигнал {check_timeout} сек — он считается вылетевшим)", parse_mode="HTML", reply_markup=kb)

# --- Управление фонами ---
@dp.callback_query(F.data == "adm_bg_menu")
async def cb_bg_menu(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить фон", callback_data="bg_add"), InlineKeyboardButton(text="🖼 Показать все", callback_data="bg_list")],
        [InlineKeyboardButton(text="🔄 Сброс на стандартный", callback_data="bg_reset")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
    ])
    status = "Свой фон" if active_bg else "Стандартный (Рандом)"
    await cb.message.edit_text(f"🖼 <b>Управление фонами:</b>\n\nАктивный режим: <b>{status}</b>\nСохранено своих фонов: <b>{len(custom_backgrounds)}</b>", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "bg_reset")
async def cb_bg_reset(cb: types.CallbackQuery):
    global active_bg; active_bg = None; await save_data(); await cb.answer("Сброшено на стандарт!", show_alert=True); await cb_bg_menu(cb)

@dp.callback_query(F.data == "bg_add")
async def cb_bg_add(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Пришли мне картинку, которую хочешь использовать как фон (как фото, не файлом):")
    await state.set_state(AdminConfig.waiting_for_bg)

@dp.message(AdminConfig.waiting_for_bg, F.photo)
async def upload_bg(m: types.Message, state: FSMContext):
    global active_bg; file_id = m.photo[-1].file_id
    if file_id not in custom_backgrounds: custom_backgrounds.append(file_id)
    active_bg = file_id; await save_data(); await state.clear()
    await m.answer("✅ Фон успешно загружен и установлен как активный!")

@dp.callback_query(F.data == "bg_list")
async def cb_bg_list(cb: types.CallbackQuery):
    if not custom_backgrounds: return await cb.answer("Нет сохраненных фонов!", show_alert=True)
    await cb.message.delete()
    for idx, f_id in enumerate(custom_backgrounds):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Выбрать этот", callback_data=f"bg_set_{idx}"), InlineKeyboardButton(text="🗑 Удалить", callback_data=f"bg_del_{idx}")]])
        marker = " (Активный)" if active_bg == f_id else ""
        await bot.send_photo(cb.message.chat.id, f_id, caption=f"Фон #{idx+1}{marker}", reply_markup=kb)
    await bot.send_message(cb.message.chat.id, "Управление закончено?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Вернуться в админку", callback_data="adm_back")]]))

@dp.callback_query(F.data.startswith("bg_set_"))
async def cb_bg_set(cb: types.CallbackQuery):
    global active_bg; idx = int(cb.data.split("_")[2])
    if idx < len(custom_backgrounds): active_bg = custom_backgrounds[idx]; await save_data(); await cb.answer(f"Фон #{idx+1} установлен!")

@dp.callback_query(F.data.startswith("bg_del_"))
async def cb_bg_del(cb: types.CallbackQuery):
    global active_bg; idx = int(cb.data.split("_")[2])
    if idx < len(custom_backgrounds):
        if active_bg == custom_backgrounds[idx]: active_bg = None
        custom_backgrounds.pop(idx); await save_data(); await cb.message.delete(); await cb.answer("Фон удален!")

# --- Старые админские команды (тест вылета, рассылка) ---
@dp.callback_query(F.data == "adm_test_dc_menu")
async def cb_test_dc_menu(cb: types.CallbackQuery):
    if not accounts: return await cb.answer("Нет аккаунтов онлайн!", show_alert=True)
    kb = [[InlineKeyboardButton(text=acc, callback_data=f"tdc_{acc}")] for acc in accounts.keys()] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]]
    await cb.message.edit_text("🧪 Выбери аккаунт для теста вылета:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("tdc_"))
async def cb_test_dc_exec(cb: types.CallbackQuery):
    acc = cb.data.replace("tdc_", "")
    if acc in accounts: accounts.pop(acc, None); await cb.answer(f"Тест: {acc} отключен!", show_alert=True)
    await refresh_panels()

@dp.callback_query(F.data == "adm_broadcast")
async def adm_bc(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📝 С заголовком", callback_data="bc_t_yes"), InlineKeyboardButton(text="💬 Без", callback_data="bc_t_no")], [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]])
    await cb.message.edit_text("Выберите формат сообщения:", reply_markup=kb)

@dp.callback_query(F.data.startswith("bc_t_"))
async def bc_step1(cb: types.CallbackQuery, state: FSMContext):
    use_t = cb.data == "bc_t_yes"; await state.update_data(has_title=use_t)
    await cb.message.edit_text("Введите заголовок:" if use_t else "Введите текст:"); await state.set_state(PostCreation.waiting_for_title if use_t else PostCreation.waiting_for_text)

@dp.message(PostCreation.waiting_for_title)
async def bc_title(m: types.Message, state: FSMContext):
    await state.update_data(title=m.text); await m.answer("Введите текст:"); await state.set_state(PostCreation.waiting_for_text)

@dp.message(PostCreation.waiting_for_text)
async def bc_text(m: types.Message, state: FSMContext):
    await state.update_data(text=m.text); await m.answer("Пришлите фото или пропустите:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить ⏩", callback_data="bc_skip_photo")]]))
    await state.set_state(PostCreation.waiting_for_photo)

@dp.callback_query(F.data == "bc_skip_photo")
async def bc_skip_p(cb: types.CallbackQuery, state: FSMContext):
    await state.update_data(photo_id=None); await show_bc_preview(cb.message, state)

@dp.message(PostCreation.waiting_for_photo, F.photo)
async def bc_photo(m: types.Message, state: FSMContext):
    await state.update_data(photo_id=m.photo[-1].file_id); await show_bc_preview(m, state)

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
    for cid in status_messages:
        try:
            if d.get("photo_id"): await bot.send_photo(cid, d["photo_id"], caption=text, parse_mode="HTML")
            else: await bot.send_message(cid, text, parse_mode="HTML")
        except: pass
    await cb.message.answer("✅ Отправлено."); add_log("Админ сделал рассылку."); await state.clear()

@dp.callback_query(F.data == "bc_cancel")
async def bc_cancel(cb: types.CallbackQuery, state: FSMContext): await state.clear(); await cb.message.delete()

# --- Настройки и Тех Перерыв ---
@dp.callback_query(F.data == "ask_reset")
async def tech_root(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 Тех. перерыв", callback_data="tp_menu")], [InlineKeyboardButton(text="⚠️ Сбросить сессию", callback_data="reset_session")], [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_only")]])
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data == "refresh_only")
async def cb_refresh(cb: types.CallbackQuery): await refresh_panels(); await cb.answer("Обновлено!")

@dp.callback_query(F.data == "reset_session")
async def cb_reset_s(cb: types.CallbackQuery):
    global session_restarts; session_restarts = 0; initial_honey.clear(); disconnect_counts.clear()
    add_log("Сброшена текущая сессия"); await save_data(); await refresh_panels(); await cb.answer("Сброшено")

@dp.callback_query(F.data == "tp_menu")
async def tp_menu(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Создать", callback_data="tp_add"), InlineKeyboardButton(text="🗑 Удалить все", callback_data="tp_clear_all")], [InlineKeyboardButton(text="⬅️ Назад", callback_data="ask_reset")]])
    await cb.message.edit_text("🛠 <b>Паузы:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "tp_add")
async def tp_add_start(cb: types.CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="ВСЕ", callback_data="target_all")]]
    for acc in notifications: kb.append([InlineKeyboardButton(text=acc, callback_data=f"target_{acc}")])
    await cb.message.edit_text("Аккаунт:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); await state.set_state(TechPause.choosing_target)

@dp.callback_query(F.data.startswith("target_"), TechPause.choosing_target)
async def tp_target(cb: types.CallbackQuery, state: FSMContext):
    target = cb.data.replace("target_", ""); await state.update_data(target=target)
    await cb.message.edit_text(f"Минут для {target}?"); await state.set_state(TechPause.entering_time)

@dp.message(TechPause.entering_time)
async def tp_time(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return
    await state.update_data(mins=int(m.text))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Авто-снятие", callback_data="tp_mode_auto"), InlineKeyboardButton(text="🔒 До конца", callback_data="tp_mode_hard")]])
    await m.answer("Режим отключения паузы:", reply_markup=kb); await state.set_state(TechPause.choosing_mode)

@dp.callback_query(F.data.startswith("tp_mode_"), TechPause.choosing_mode)
async def tp_mode_fin(cb: types.CallbackQuery, state: FSMContext):
    is_auto = "auto" in cb.data; d = await state.get_data(); now = time.time()
    targets = list(notifications.keys()) if d['target'] == "all" else [d['target']]
    for t in targets: 
        pause_data[t] = {"until": now + d['mins'] * 60, "auto_off": is_auto}
        add_log(f"🛠 {t} ушел на паузу ({d['mins']}м)")
    await save_data(); await state.clear(); await cb.message.answer(f"✅ Готово."); await refresh_panels()

@dp.callback_query(F.data == "tp_clear_all")
async def tp_clear(cb: types.CallbackQuery):
    pause_data.clear(); add_log("🗑 Все паузы были сброшены вручную"); await save_data(); await cb.answer("Очищено"); await refresh_panels()


# --- Инлайн Режим ---
@dp.inline_query()
async def inline_handler(query: InlineQuery):
    text = query.query.strip().lower()
    results = []
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Обновить данные", callback_data="refresh_inline_status")
    ]])

    results.append(
        InlineQueryResultArticle(
            id="inline_status",
            title="📊 Мониторинг Улья",
            description="Текущий статус аккаунтов (текст)",
            input_message_content=InputTextMessageContent(
                message_text=get_status_text(),
                parse_mode="HTML"
            ),
            reply_markup=kb
        )
    )

    if text.startswith("img"):
        args = text.split()[1:]
        is_manual = len(args) > 0
        t_accs = args if is_manual else list(set(list(accounts.keys()) + list(pause_data.keys())))
        
        if t_accs:
            results.append(
                InlineQueryResultArticle(
                    id="inline_img_trigger",
                    title="🖼 Сгенерировать Инфо-Графику",
                    description=f"Для: {', '.join(t_accs[:3])}...",
                    input_message_content=InputTextMessageContent(
                        message_text=f"/img {' '.join(t_accs)}" 
                    )
                )
            )

    await query.answer(results, cache_time=5)

@dp.callback_query(F.data == "refresh_inline_status")
async def cb_refresh_inline(cb: types.CallbackQuery):
    if cb.inline_message_id:
        new_text = get_status_text()
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Обновить данные", callback_data="refresh_inline_status")
        ]])
        
        try:
            await bot.edit_message_text(
                inline_message_id=cb.inline_message_id,
                text=new_text,
                parse_mode="HTML",
                reply_markup=kb
            )
            await cb.answer("Данные обновлены!")
        except Exception as e:
            if "is not modified" in str(e).lower():
                await cb.answer("Изменений нет")
            else:
                await cb.answer("Ошибка обновления")


# --- Сервер и Мониторинг ---
async def handle_signal(request):
    try:
        d = await request.json(); u = d.get("username")
        if u:
            if u in pause_data and pause_data[u].get("auto_off"): 
                pause_data.pop(u, None); add_log(f"✅ Пауза {u} снята автоматически")
            
            if u not in start_times: start_times[u] = time.time(); add_log(f"🟢 {u} вошел в сеть")
            accounts[u] = time.time()
            
            raw_honey = float(d.get("honey", 0))
            if u not in initial_honey: initial_honey[u] = raw_honey
            profit = raw_honey - initial_honey[u]
            
            p, c = d.get("pollen", 0), d.get("capacity", 1)
            raw_b = int((p/c)*100)
            acc_stats[u] = {
                "h": format_honey(raw_honey), "prof": format_honey(profit),
                "raw_prof": profit, "b": f"{raw_b}%", "raw_b": raw_b
            }
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def check_timeouts():
    now = time.time()
    expired = [u for u, pd in pause_data.items() if now >= pd.get('until', 0)]
    for u in expired: pause_data.pop(u, None); add_log(f"⏱ Время паузы {u} истекло")
    
    for u in list(accounts.keys()):
        if now - accounts[u] > check_timeout:
            if u not in pause_data:
                tags = " ".join(notifications.get(u, ["!"]))
                for cid in status_messages:
                    try: await bot.send_message(cid, f"🚨 <b>{u}</b> ВЫЛЕТ!\n{tags}", parse_mode="HTML")
                    except: pass
                add_log(f"🔴 {u} вылетел")
                disconnect_counts[u] = disconnect_counts.get(u, 0) + 1
            accounts.pop(u, None); start_times.pop(u, None); acc_stats.pop(u, None)
            
    await save_data(); await refresh_panels()

async def refresh_panels():
    txt = get_status_text(); kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ Настройки", callback_data="ask_reset")]])
    for cid, mid in list(status_messages.items()):
        try: await bot.edit_message_text(txt, chat_id=int(cid), message_id=int(mid), parse_mode="HTML", reply_markup=kb)
        except: pass

async def monitor_loop():
    add_log("🚀 Сервер мониторинга запущен")
    while True:
        try: await check_timeouts()
        except: pass
        await asyncio.sleep(30)

async def main():
    await download_font(); await load_data()
    asyncio.create_task(monitor_loop())
    app = web.Application(); app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, '0.0.0.0', PORT).start()
    await bot.delete_webhook(drop_pending_updates=True); await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
