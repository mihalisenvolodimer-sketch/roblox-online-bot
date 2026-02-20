import os, asyncio, time, json, redis.asyncio as redis, aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

accounts = {}      
start_times = {}   
notifications = {} 
status_messages = {}

# --- Система логов ---
def log(text):
    print(f"DEBUG [{time.strftime('%H:%M:%S')}]: {text}")

async def load_data():
    global db, notifications, status_messages
    if not REDIS_URL: 
        log("REDIS_URL не найден! Работаем без базы.")
        return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V26_DEBUG")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            log("Данные успешно загружены из Redis")
    except Exception as e:
        log(f"Ошибка Redis: {e}")

async def save_data():
    if not db: return
    try:
        data = {"notifs": notifications, "msgs": status_messages}
        await db.set("BSS_V26_DEBUG", json.dumps(data))
    except: pass

# --- Команды ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    log(f"Команда /start от {m.from_user.id}")
    await m.answer("🐝 Бот v26 онлайн. Используй /Information")

@dp.message(Command("Information"))
async def cmd_info(m: types.Message):
    log(f"Создание панели в чате {m.chat.id}")
    msg = await m.answer("<b>🐝 Ожидание первого сигнала...</b>", parse_mode="HTML")
    status_messages[str(m.chat.id)] = msg.message_id
    try:
        await bot.pin_chat_message(m.chat.id, msg.message_id, disable_notification=True)
        log("Сообщение успешно закреплено")
    except Exception as e:
        log(f"Не удалось закрепить: {e}")
    await save_data()

@dp.message(Command("add"))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("Пиши: /add Ник")
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    notifications.setdefault(acc, []).append(tag)
    await save_data()
    await m.answer(f"✅ Пинг для {acc} добавлен")

# --- Мониторинг ---

async def update_loop():
    while True:
        now = time.time()
        # Проверка вылетов
        for u in list(accounts.keys()):
            if now - accounts[u] > 180:
                log(f"Аккаунт {u} признан вылетевшим")
                if u in notifications:
                    for cid in status_messages:
                        try: await bot.send_message(int(cid), f"🚨 {u} ВЫЛЕТЕЛ!\n{' '.join(notifications[u])}")
                        except: pass
                accounts.pop(u)
                start_times.pop(u, None)

        # Текст
        text = f"<b>🐝 Состояние Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')}\n\n"
        if not accounts:
            text += "<i>Сигналов нет. Проверь макрос!</i>"
        else:
            for u in sorted(accounts.keys()):
                dur = int(now - start_times.get(u, now))
                text += f"🟢 <code>{u}</code> | {dur//60}m {dur%60}s\n"

        for cid, mid in list(status_messages.items()):
            try:
                await bot.edit_message_text(text, int(cid), int(mid), parse_mode="HTML")
            except Exception as e:
                pass # Ошибки редактирования игнорим
        
        await save_data()
        await asyncio.sleep(20)

async def handle_signal(request):
    try:
        data = await request.json()
        u = data.get("username")
        if u:
            log(f"--- ПОЛУЧЕН СИГНАЛ ОТ {u} ---")
            if u not in accounts: start_times[u] = time.time()
            accounts[u] = time.time()
            return web.Response(text="OK")
    except Exception as e:
        log(f"Ошибка входящего сигнала: {e}")
    return web.Response(status=400)

async def main():
    log("Запуск бота...")
    await load_data()
    asyncio.create_task(update_loop())
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    log(f"HTTP сервер запущен на порту {PORT}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
