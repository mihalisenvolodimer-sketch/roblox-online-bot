import os
import asyncio
import time
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiohttp import web

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Данные
accounts = {}      # { "roblox_name": last_seen_timestamp }
notifications = {} # { "roblox_name": telegram_id }
last_status = {}   # { "roblox_name": last_known_online_state }
status_chat_id = None
status_message_id = None

async def reset_status_msg(chat_id):
    global status_chat_id, status_message_id
    # Пытаемся удалить старое
    if status_chat_id and status_message_id:
        try: await bot.delete_message(status_chat_id, status_message_id)
        except: pass
    
    status_chat_id = chat_id
    msg = await bot.send_message(chat_id, "⏳ Запуск мониторинга...")
    status_message_id = msg.message_id
    
    try:
        await bot.pin_chat_message(chat_id, status_message_id, disable_notification=True)
        await asyncio.sleep(1)
        await bot.delete_message(chat_id, status_message_id + 1)
    except: pass

@dp.message(Command("add"))
async def add_notify(message: types.Message, command: CommandObject):
    if not command.args:
        return await message.answer("Использование: `/add НикRoblox @юзер`", parse_mode="Markdown")
    
    args = command.args.split()
    if len(args) < 2:
        return await message.answer("Нужно указать и ник, и упомянуть пользователя!")
    
    rbx_name = args[0]
    # Проверяем наличие упоминания
    if not message.entities:
        return await message.answer("Нужно именно упомянуть пользователя через @")
    
    user_id = None
    for entity in message.entities:
        if entity.type == "mention":
            # В aiogram 3 вытаскиваем текст упоминания
            mention = message.text[entity.offset:entity.offset+entity.length]
            # Это костыль, так как API не дает ID по @нику напрямую боту, 
            # если юзер не писал боту. Поэтому лучше если юзер сам напишет /add Ник @me
            user_id = mention 
        elif entity.type == "text_mention":
            user_id = entity.user.mention_html(entity.user.full_name)

    notifications[rbx_name] = user_id
    await message.answer(f"✅ Уведомления для `{rbx_name}` установлены на {user_id}", parse_mode="Markdown")

@dp.message(Command("remove"))
async def remove_notify(message: types.Message, command: CommandObject):
    args = command.args
    if not args: return await message.answer("Укажите ник Roblox")
    if args in notifications:
        del notifications[args]
        await message.answer(f"❌ Пинги для `{args}` отключены.")

@dp.message(Command("delete"))
async def delete_all(message: types.Message):
    # Удаляет до 50 последних сообщений бота в чате
    for i in range(0, 50):
        try:
            await bot.delete_message(message.chat.id, message.message_id - i)
        except:
            continue
    await message.answer("🧹 Чат очищен от старых логов.")

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    try: await message.delete()
    except: pass
    await reset_status_msg(message.chat.id)

async def update_status_message():
    global status_message_id, status_chat_id
    if not status_chat_id or not status_message_id: return

    current_time = time.time()
    text = "📊 **Мониторинг Roblox**\n"
    text += f"Обновлено: {time.strftime('%H:%M:%S')}\n\n"
    
    sorted_users = sorted(accounts.keys())
    for user in sorted_users:
        is_online = current_time - accounts[user] < 90
        
        # Проверка на вылет для пинга
        if user in last_status and last_status[user] == True and not is_online:
            if user in notifications:
                try:
                    await bot.send_message(status_chat_id, f"⚠️ Аккаунт **{user}** ВЫЛЕТЕЛ! {notifications[user]}", parse_mode="Markdown")
                except: pass
        
        last_status[user] = is_online
        status = "🟢 В игре" if is_online else "🔴 Вылетел"
        text += f"👤 `{user}`: {status}\n"

    try:
        await bot.edit_message_text(text, status_chat_id, status_message_id, parse_mode="Markdown")
    except Exception as e:
        if "message to edit not found" in str(e).lower(): status_message_id = None

async def handle_signal(request):
    try:
        data = await request.json()
        if "username" in data:
            accounts[data["username"]] = time.time()
            return web.Response(text="OK")
    except: pass
    return web.Response(text="Error", status=400)

async def status_updater():
    while True:
        await update_status_message()
        await asyncio.sleep(15)

async def main():
    app = web.Application()
    app.router.add_post('/signal', handle_signal)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    asyncio.create_task(status_updater())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
