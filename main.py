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

# --- Настройки ---
VERSION = "V5.2 FINAL"
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))
ALLOWED_ADMIN = "Gold_mod1" 
DB_KEY = "BSS_GLOBAL_DATABASE_PRO" # ВЕЧНЫЙ КЛЮЧ БАЗЫ ДАННЫХ
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
total_restarts, session_restarts = 0, 0

BG_URLS = ["https://wallpaperaccess.com/full/7500647.png", "https://wallpaperaccess.com/full/14038208.jpg"]

class PostCreation(StatesGroup):
    waiting_for_title = State(); waiting_for_text = State(); waiting_for_photo = State(); confirming = State()

class TechPause(StatesGroup):
    choosing_target = State(); entering_time = State()

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
        raw = await db.get(DB_KEY)
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
        await db.set(DB_KEY, json.dumps({
            "notifs": notifications, "msgs": status_messages, "total_restarts": total_restarts,
            "session_restarts": session_restarts, "starts": start_times, "accounts": accounts,
            "pause_data": pause_data
        }))
    except: pass

# --- Отрисовка ---
async def generate_status_image(target_accounts, is_online_mode=True):
    width, row_h, head_h, foot_h = 750, 115, 130, 80
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
    draw.text((45, 40), "ОНЛАЙН МОНИТОРИНГ", font=f_l, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0,0,0))
    now = time.time()
    async with aiohttp.ClientSession() as session:
        for i, acc in enumerate(target_accounts):
            y = head_h + (i * row_h)
            draw.rounded_rectangle([30, y, width-30, y+row_h-10], fill=(0, 0, 0, 180), radius=15)
            av = await get_avatar(acc, session)
            if av: img.paste(av.resize((85, 85)), (45, y+10), av.resize((85, 85)))
            draw.text((145, y+15), acc, font=f_m, fill=(255, 255, 255))
            st = acc_stats.get(acc, {"h": "0", "b": "0%"})
            draw.text((145, y+55), f"Honey: {st['h']} | Bag: {st['b']}", font=f_s, fill=(200, 200, 200))
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
                text += f"🛠 <code>{u}</code> | <b>ПАУЗА ({rem//60}м)</b>\n"
            elif u in accounts:
                d = int(now - start_times.get(u, now))
                st = acc_stats.get(u, {"h": "0", "b": "0%"})
                text += f"🟢 <code>{u}</code> | 🍯 <b>{st['h']}</b> | 🎒 <b>{st['b']}</b>\n"
                text += f"└ 🕒 <b>{d//3600}ч {(d%3600)//60}м в сети</b>\n\n"
    return text

# --- Хендлеры ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    # Добавлено отображение общих рестартов
    await m.answer(f"<b>🐝 BSS {VERSION}</b>\n🔄 Общих рестартов бота: <b>{total_restarts}</b>\n\n/information — Статус\n/img — Картинка\n/list — Пинги\n/add [Ник] [Тег]", parse_mode="HTML")

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
    args = m.text.split()[1:]
    is_on = len(args) == 0
    t_accs = list(set(list(accounts.keys()) + list(pause_data.keys()))) if is_on else args
    if not t_accs: return await m.answer("Список пуст.")
    msg = await m.answer("🎨 Рисую...")
    try:
        img_bytes = await generate_status_image(t_accs, is_online_mode=is_on)
        await m.answer_photo(photo=BufferedInputFile(file=img_bytes, filename="bss.png"))
        await msg.delete()
    except Exception as e: await msg.edit_text(f"Ошибка: {e}")

@dp.message(Command("list"))
async def cmd_list(m: types.Message):
    if not notifications: return await m.answer("Список пингов пуст. Добавь их через /add!")
    res = "<b>📜 Список уведомлений (Пинги):</b>\n"
    for acc, tags in notifications.items(): res += f"• <code>{acc}</code>: {', '.join(tags)}\n"
    await m.answer(res, parse_mode="HTML")

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Формат: /add Ник @тег")
    acc = args[1]
    
    # Если тег не указан, берем @username пользователя (или ID, если юзернейма нет)
    if len(args) > 2:
        tag = args[2]
    else:
        tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
        
    if acc not in notifications: notifications[acc] = []
    if tag not in notifications[acc]:
        notifications[acc].append(tag)
    
    await save_data()
    await m.answer(f"✅ <b>{acc}</b> добавлен в базу с пингом <b>{tag}</b>.", parse_mode="HTML")

@dp.message(Command("remove"))
async def cmd_remove(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Ник?")
    if args[1] in notifications:
        del notifications[args[1]]; await save_data(); await m.answer(f"❌ {args[1]} удален из базы.")

# --- Админка ---
@dp.message(Command("adm"))
async def cmd_adm(m: types.Message):
    if m.from_user.username != ALLOWED_ADMIN: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="🧪 Тест вылета", callback_data="adm_test_dc")]
    ])
    await m.answer("🕹 <b>Панель администратора:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "adm_test_dc")
async def cb_test_dc(cb: types.CallbackQuery):
    if not accounts: return await cb.answer("Нет аккаунтов онлайн!", show_alert=True)
    acc = list(accounts.keys())[0]
    accounts.pop(acc, None); await cb.answer(f"Тест: {acc} отключен!", show_alert=True)
    await refresh_panels()

# --- Рассылка ---
@dp.callback_query(F.data == "adm_broadcast")
async def adm_bc(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📝 С заголовком", callback_data="bc_t_yes"), InlineKeyboardButton(text="💬 Без", callback_data="bc_t_no")]])
    await cb.message.edit_text("Выберите формат сообщения:", reply_markup=kb)

@dp.callback_query(F.data.startswith("bc_t_"))
async def bc_step1(cb: types.CallbackQuery, state: FSMContext):
    use_t = cb.data == "bc_t_yes"
    await state.update_data(has_title=use_t)
    await cb.message.edit_text("Введите заголовок:" if use_t else "Введите текст:")
    await state.set_state(PostCreation.waiting_for_title if use_t else PostCreation.waiting_for_text)

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
    await cb.message.answer("✅ Отправлено."); await state.clear()

@dp.callback_query(F.data == "bc_cancel")
async def bc_cancel(cb: types.CallbackQuery, state: FSMContext):
    await state.clear(); await cb.message.delete(); await cb.answer("Рассылка отменена")

# --- Настройки ---
@dp.callback_query(F.data == "ask_reset")
async def tech_root(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Тех. перерыв", callback_data="tp_menu")],
        [InlineKeyboardButton(text="⚠️ Сбросить сессию", callback_data="reset_session")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_only")]
    ])
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data == "refresh_only")
async def cb_refresh(cb: types.CallbackQuery):
    await refresh_panels(); await cb.answer("Данные обновлены!")

@dp.callback_query(F.data == "reset_session")
async def cb_reset_s(cb: types.CallbackQuery):
    global session_restarts; session_restarts = 0; await save_data(); await refresh_panels(); await cb.answer("Сессия сброшена")

@dp.callback_query(F.data == "tp_menu")
async def tp_menu(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Создать", callback_data="tp_add"), InlineKeyboardButton(text="🗑 Удалить все", callback_data="tp_clear_all")], [InlineKeyboardButton(text="⬅️ Назад", callback_data="ask_reset")]])
    await cb.message.edit_text("🛠 <b>Паузы:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "tp_add")
async def tp_add_start(cb: types.CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="ВСЕ", callback_data="target_all")]]
    for acc in notifications: kb.append([InlineKeyboardButton(text=acc, callback_data=f"target_{acc}")])
    await cb.message.edit_text("Выбери аккаунт:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(TechPause.choosing_target)

@dp.callback_query(F.data.startswith("target_"), TechPause.choosing_target)
async def tp_target(cb: types.CallbackQuery, state: FSMContext):
    target = cb.data.replace("target_", ""); await state.update_data(target=target)
    await cb.message.edit_text(f"Минут для <b>{target}</b>?", parse_mode="HTML")
    await state.set_state(TechPause.entering_time)

@dp.message(TechPause.entering_time)
async def tp_time(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return
    mins = int(m.text); d = await state.get_data(); now = time.time()
    targets = list(notifications.keys()) if d['target'] == "all" else [d['target']]
    for t in targets: pause_data[t] = {"until": now + mins * 60, "auto_off": True}
    await save_data(); await m.answer(f"✅ Пауза {mins}м."); await state.clear(); await refresh_panels()

@dp.callback_query(F.data == "tp_clear_all")
async def tp_clear(cb: types.CallbackQuery):
    pause_data.clear(); await save_data(); await cb.answer("Очищено"); await refresh_panels()

# --- Логика сервера ---
async def handle_signal(request):
    try:
        d = await request.json(); u = d.get("username")
        if u:
            if u in pause_data and pause_data[u].get("auto_off"): pause_data.pop(u, None)
            if u not in start_times: start_times[u] = time.time()
            accounts[u] = time.time()
            p, c = d.get("pollen", 0), d.get("capacity", 1)
            # Если не хочешь чтобы было больше 100%, можно заменить на min(int((p/c)*100), 100)
            acc_stats[u] = {"h": format_honey(d.get("honey", 0)), "b": f"{int((p/c)*100)}%"}
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def check_timeouts():
    now = time.time()
    
    # --- Очистка истекших пауз ---
    expired_pauses = [u for u, pd in pause_data.items() if now >= pd.get('until', 0)]
    for u in expired_pauses:
        pause_data.pop(u, None)
        
    for u in list(accounts.keys()):
        if now - accounts[u] > 120:
            tags = " ".join(notifications.get(u, ["!"]))
            for cid in status_messages:
                try: await bot.send_message(cid, f"🚨 <b>{u}</b> ВЫЛЕТ!\n{tags}", parse_mode="HTML")
                except: pass
            accounts.pop(u, None); start_times.pop(u, None); acc_stats.pop(u, None)
            
    await save_data(); await refresh_panels()

async def refresh_panels():
    txt = get_status_text(); kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ Настройки", callback_data="ask_reset")]])
    for cid, mid in list(status_messages.items()):
        try: await bot.edit_message_text(txt, chat_id=int(cid), message_id=int(mid), parse_mode="HTML", reply_markup=kb)
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
