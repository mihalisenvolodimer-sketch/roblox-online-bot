import os
import asyncio
import time
import json
import redis.asyncio as redis
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web

# --- Настройки ---
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Данные в памяти
accounts = {}      
start_times = {}   
notifications = {} 
status_messages = {}

def logger(msg):
    print(f"DEBUG [{time.strftime('%H:%M:%S')}]: {msg}")

# --- База Данных ---
async def load_data():
    global db, notifications, status_messages
    if not REDIS_URL:
        logger("REDIS_URL не найден, работаем без БД")
        return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V31_LOGS")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            logger(f"Загружено из БД: {len(status_messages)} активных чатов")
    except Exception as e:
        logger(f"Ошибка загрузки БД: {e}")

async def save_data():
    if not db: return
    try:
        data = {"notifs": notifications, "msgs": status_messages}
        await db.set("BSS_V31_LOGS", json.dumps(data))
    except Exception as e:
        logger(f"Ошибка сохранения БД: {e}")

# --- Текст ---
def get_status_text():
    now = time.time()
    text = f"<b>🐝 Состояние Улья BSS</b>\n"
    text += f"🕒 Обновлено: {time.strftime('%H:%M:%S')}\n\n"
    if not accounts:
        text += "<i>Ожидание сигналов от макросов...</i>"
    else:
        for u in sorted(accounts.keys()):
            dur = int(now - start_times.get(u, now))
            h = dur // 3600
            m = (dur % 3600) // 60
            s = dur % 60
            text += f"🟢 <code>{u}</code> | <b>{h}ч {m}м {s}с</b>\n"
    return text

async def refresh_panels():
    """Функция, которая ищет все панели и обновляет их текст"""
    if not status_messages:
        return

    text = get_status_text()
    for cid, mid in list(status_messages.items()):
        try:
            logger(f"Обновляю сообщение {mid} в чате {cid}")
            await bot.edit_message_text(
                text=text,
                chat_id=int(cid),
                message_id=int(mid),
                parse_mode="HTML"
            )
        except Exception as e:
            if "message is not modified" in str(e):
                pass # Это нормально, если данные не изменились
            else:
                logger(f"Ошибка при обновлении {cid}: {e}")
                # Если сообщение удалено, убираем его из списка, чтобы не спамить ошибками
                if "message to edit not found" in str(e) or "chat not found" in str(e):
                    status_messages.pop(cid, None)

# --- Команды ---

@dp.message(Command("start", ignore_case=True))
async def cmd_start(m: types.Message):
    await m.answer("🐝 Бот Улья запущен! Используй /information")

@dp.message(Command("information", ignore_case=True))
async def cmd_info(m: types.Message):
    cid = str(m.chat.id)
    logger(f"Команда /information получена в {cid}")
    
    # Пытаемся удалить старое, если оно есть
    if cid in status_messages:
        try:
            await bot.delete_message(m.chat.id, status_messages[cid])
            logger(f"Старое сообщение {status_messages[cid]} удалено")
        except:
            pass

    msg = await m.answer(get_status_text(), parse_mode="HTML")
    status_messages[cid] = msg.message_id
    logger(f"Новое сообщение создано: {msg.message_id}")
    
    try:
        await bot.pin_chat_message(m.chat.id, msg.message_id, disable_notification=True)
        # Удаляем системный текст о закрепе через секунду
        await asyncio.sleep(1)
        await bot.delete_message(m.chat.id, msg.message_id + 1)
    except Exception as e:
        logger(f"Ошибка закрепа: {e}")
        
    await save_data()

@dp.message(Command("add", ignore_case=True))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2:
        return await m.answer("Используй: /add Ник")
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    notifications.setdefault(acc, []).append(tag)
    logger(f"Добавлен пинг для {acc}: {tag}")
    await save_data()
    await m.answer(f"✅ Пинг для {acc} добавлен")

# --- Сервер сигналов ---

async def handle_signal(request):
    try:
        data = await request.json()
        u = data.get("username")
        if u:
            now = time.time()
            if u not in accounts:
                start_times[u] = now
                logger(f"Аккаунт {u} зашел в сеть")
            accounts[u] = now
            # Мгновенно обновляем панель при сигнале
            asyncio.create_task(refresh_panels())
            return web.Response(text="OK")
    except Exception as e:
        logger(f"Ошибка во входящем сигнале: {e}")
    return web.Response(status=400)

async def update_loop():
    """Фоновый цикл: обновляет время раз в 30 сек и проверяет вылеты"""
    while True:
        try:
            now = time.time()
            for u in list(accounts.keys()):
                if now - accounts[u] > 180: # 3 минуты тишины
                    logger(f"Аккаунт {u} потерян (вылет)")
                    if u in notifications:
                        for cid in status_messages:
                            try:
                                await bot.send_message(int(cid), f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{' '.join(notifications[u])}", parse_mode="HTML")
                            except: pass
                    accounts.pop(u)
                    start_times.pop(u, None)
            
            await refresh_panels()
            await save_data()
        except Exception as e:
            logger(f"Ошибка в цикле мониторинга: {e}")
        await asyncio.sleep(30)

async def main():
    logger("Инициализация бота...")
    await load_data()
    asyncio.create_task(update_loop())
    
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    logger(f"HTTP сервер запущен на порту {PORT}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
