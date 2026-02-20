import os
import asyncio
import time
import json
import redis.asyncio as redis
import aiohttp
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
last_text = {} 

def logger(msg):
    print(f"DEBUG [{time.strftime('%H:%M:%S')}]: {msg}")

# --- База Данных ---
async def load_data():
    global db, notifications, status_messages
    if not REDIS_URL: return
    try:
        db = redis.from_url(REDIS_URL, decode_responses=True)
        raw = await db.get("BSS_V34_STABLE")
        if raw:
            data = json.loads(raw)
            notifications.update(data.get("notifs", {}))
            status_messages.update(data.get("msgs", {}))
            logger("Данные успешно загружены из Redis")
    except Exception as e:
        logger(f"Ошибка БД при загрузке: {e}")

async def save_data():
    if not db: return
    try:
        data = {"notifs": notifications, "msgs": status_messages}
        await db.set("BSS_V34_STABLE", json.dumps(data))
    except Exception as e:
        logger(f"Ошибка БД при сохранении: {e}")

# --- Логика текста ---
def get_status_text():
    now = time.time()
    text = f"<b>🐝 Состояние Улья BSS</b>\n"
    text += f"🕒 Обновлено: {time.strftime('%H:%M:%S')}\n\n"
    if not accounts:
        text += "<i>Ожидание сигналов от макросов...</i>"
    else:
        for u in sorted(accounts.keys()):
            dur = int(now - start_times.get(u, now))
            h, m, s = dur//3600, (dur%3600)//60, dur%60
            text += f"🟢 <code>{u}</code> | <b>{h}ч {m}м {s}с</b>\n"
    return text

async def refresh_panels():
    if not status_messages: return
    text = get_status_text()
    for cid, mid in list(status_messages.items()):
        if last_text.get(str(cid)) == text: continue
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=str(cid), 
                message_id=int(mid),
                parse_mode="HTML"
            )
            last_text[str(cid)] = text
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                logger(f"Ошибка обновления панели в {cid}: {e}")

# --- Обработка команд ---

@dp.message(Command("start", ignore_case=True))
async def cmd_start(m: types.Message):
    logger(f"Пользователь {m.from_user.id} нажал /start")
    welcome_text = (
        "<b>🐝 Бот Улья приветствует тебя!</b>\n\n"
        "📜 <b>Команды:</b>\n"
        "/information — Создать панель мониторинга\n"
        "/add [Ник] — Подписаться на уведомления о вылете\n"
        "/delete [Ник] — Удалить подписку на ник\n"
        "/list — Показать все активные подписки"
    )
    await m.answer(welcome_text, parse_mode="HTML")

@dp.message(Command("information", ignore_case=True))
async def cmd_info(m: types.Message):
    cid_str = str(m.chat.id)
    logger(f"Запрос панели /information в чате {cid_str}")
    
    if cid_str in status_messages:
        try:
            await bot.delete_message(chat_id=cid_str, message_id=status_messages[cid_str])
            logger(f"Старая панель в {cid_str} удалена")
        except: pass

    msg = await m.answer(get_status_text(), parse_mode="HTML")
    status_messages[cid_str] = msg.message_id
    
    try:
        await bot.pin_chat_message(chat_id=cid_str, message_id=msg.message_id, disable_notification=True)
        await asyncio.sleep(1)
        await bot.delete_message(chat_id=cid_str, message_id=msg.message_id + 1)
    except Exception as e:
        logger(f"Ошибка закрепа в {cid_str}: {e}")
    
    await save_data()

@dp.message(Command("add", ignore_case=True))
async def cmd_add(m: types.Message):
    args = m.text.split()
    if len(args) < 2:
        return await m.answer("⚠️ Пиши ник: <code>/add PlayerName</code>", parse_mode="HTML")
    
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    
    if acc not in notifications:
        notifications[acc] = []
    
    if tag not in notifications[acc]:
        notifications[acc].append(tag)
        logger(f"Добавлен пинг: {acc} -> {tag}")
        await save_data()
        await m.answer(f"✅ Пинг для <b>{acc}</b> добавлен пользователю {tag}", parse_mode="HTML")
    else:
        await m.answer(f"ℹ️ Ты уже подписан на {acc}")

@dp.message(Command("delete", ignore_case=True))
async def cmd_delete(m: types.Message):
    args = m.text.split()
    if len(args) < 2:
        return await m.answer("⚠️ Пиши ник: <code>/delete PlayerName</code>", parse_mode="HTML")
    
    acc = args[1]
    tag = f"@{m.from_user.username}" if m.from_user.username else f"ID:{m.from_user.id}"
    
    if acc in notifications and tag in notifications[acc]:
        notifications[acc].remove(tag)
        if not notifications[acc]: # Если подписчиков больше нет, удаляем ник совсем
            del notifications[acc]
        logger(f"Удален пинг: {acc} -> {tag}")
        await save_data()
        await m.answer(f"❌ Пинг для <b>{acc}</b> удален.", parse_mode="HTML")
    else:
        await m.answer(f"❓ Ты не подписан на <b>{acc}</b>", parse_mode="HTML")

@dp.message(Command("list", ignore_case=True))
async def cmd_list(m: types.Message):
    logger(f"Запрос списка подписок в чате {m.chat.id}")
    if not notifications:
        return await m.answer("Список подписок пуст.")
    
    res = "<b>📜 Активные уведомления:</b>\n"
    for acc, tags in notifications.items():
        res += f"• <code>{acc}</code>: {', '.join(tags)}\n"
    await m.answer(res, parse_mode="HTML")

# --- Сервер сигналов и Мониторинг ---

async def handle_signal(request):
    try:
        data = await request.json()
        u = data.get("username")
        if u:
            if u not in accounts:
                start_times[u] = time.time()
                logger(f"Аккаунт {u} ПОДКЛЮЧИЛСЯ")
            accounts[u] = time.time()
            asyncio.create_task(refresh_panels())
            return web.Response(text="OK")
    except Exception as e:
        logger(f"Ошибка сигнала: {e}")
    return web.Response(status=400)

async def monitor():
    while True:
        now = time.time()
        for u in list(accounts.keys()):
            if now - accounts[u] > 180: # 3 минуты тишины
                logger(f"Аккаунт {u} ВЫЛЕТЕЛ (нет сигнала)")
                if u in notifications:
                    for cid in status_messages:
                        try: 
                            await bot.send_message(
                                chat_id=str(cid), 
                                text=f"🚨 <b>{u}</b> ВЫЛЕТЕЛ!\n{' '.join(notifications[u])}", 
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger(f"Не удалось отправить уведомление в {cid}: {e}")
                accounts.pop(u)
                start_times.pop(u, None)
        await refresh_panels()
        await save_data()
        await asyncio.sleep(30)

async def main():
    logger("Запуск системы v34...")
    await load_data()
    asyncio.create_task(monitor())
    
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    await asyncio.sleep(1) 
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
