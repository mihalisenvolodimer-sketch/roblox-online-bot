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

# Данные
accounts = {}      
start_times = {}   
notifications = {} 
status_messages = {}

# --- База Данных ---
async def load_data():
    global db, notifications, status_messages
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V30_ULTRA")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
    except: pass

async def save_data():
    if not db: return
    try:
        data = {"notifs": notifications, "msgs": status_messages}
        await db.set("BSS_V30_ULTRA", json.dumps(data))
    except: pass

# --- Логика Текста ---
def get_status_text():
    now = time.time()
    text = f"<b>🐝 Состояние Улья BSS</b>\n🕒 {time.strftime('%H:%M:%S')}\n\n"
    if not accounts:
        text += "<i>Пчелы спят. Ожидание макросов...</i>"
    else:
        for u in sorted(accounts.keys()):
            dur = int(now - start_times.get(u, now))
            h, m, s = dur//3600, (dur%3600)//60, dur%60
            text += f"🟢 <code>{u}</code> | <b>{h}ч {m}м {s}с</b>\n"
    return text

async def force_refresh():
    text = get_status_text()
    for cid, mid in list(status_messages.items()):
        try:
            await bot.edit_message_text(text, int(cid), int(mid), parse_mode="HTML")
        except Exception:
            pass # Если сообщение удалено или нет изменений

# --- Команды ---

@dp.message(Command("start", ignore_case=True))
async def cmd_start(m: types.Message):
    await m.answer("🐝 Бот Улья v30 готов. Используй /information")

@dp.message(Command("information", ignore_case=True))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    
    # 1. Пытаемся удалить старую панель, если она была
    if cid in status_messages:
        try:
            await bot.delete_message(m.chat.id, status_messages[cid])
        except:
            pass

    # 2. Создаем новую
    msg = await m.answer(get_status_text(), parse_mode="HTML")
    status_messages[cid] = msg.message_id
    
    try:
        await bot.pin_chat_message(m.chat.id, msg.message_id, disable_notification=True)
        # Удаляем сервисное сообщение "закрепил сообщение"
        await asyncio.sleep(1)
        await bot.delete_message(m.chat.id, msg.message_id + 1)
    except:
        pass
        
    await save_data()

@dp.message(Command("add", ignore_case=True))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2:
        return await m.answer("Напиши: /add Ник")
    
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    
    if acc not in notifications: notifications[acc] = []
    if tag not in notifications[acc]: notifications[acc].append(tag)
    
    await save_data()
    await m.answer(f"✅ Пинг для <b>{acc}</b> добавлен.", parse_mode="HTML")

@dp.message(Command("call", ignore_case=True))
async def cmd_call(m: types.Message):
    tags = set()
    for t_list in notifications.values():
        for t in t_list: tags.add(t)
    if tags:
        await m.answer(f"📣 <b>СБОР УЛЬЯ:</b>\n\n{' '.join(tags)}", parse_mode="HTML")

# --- Потоки данных ---

async def handle_signal(request):
    try:
        data = await request.json()
        u = data.get("username")
        if u:
            now = time.time()
            if u not in accounts:
                start_times[u] = now
            accounts[u] = now
            # Обновляем сразу при получении сигнала
            asyncio.create_task(force_refresh())
            return web.Response(text="OK")
    except: pass
    return web.Response(status=400)

async def update_loop():
    """Фоновый цикл обновления времени и проверки вылетов"""
    while True:
        try:
            now = time.time()
            # Проверка вылетов (180 сек тишины)
            for u in list(accounts.keys()):
                if now - accounts[u] > 180:
                    if u in notifications:
                        for cid in status_messages:
                            try:
                                await bot.send_message(int(cid), f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{' '.join(notifications[u])}", parse_mode="HTML")
                            except: pass
                    accounts.pop(u)
                    start_times.pop(u, None)
            
            await force_refresh()
            await save_data()
        except Exception as e:
            print(f"Ошибка цикла: {e}")
            
        await asyncio.sleep(30)

async def main():
    await load_data()
    asyncio.create_task(update_loop())
    
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
