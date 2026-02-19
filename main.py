import requests
import asyncio
import json
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TOKEN")
CHECK_INTERVAL = 30  # Проверяем каждые 30 секунд
DATA_FILE = "players.json"

tracked_players = {}  # {chat_id: {username: message_id}}
player_status = {}  # {chat_id: {username: online_status}}

def load_data():
    global tracked_players
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                tracked_players = json.load(f)
        except:
            tracked_players = {}
    else:
        tracked_players = {}

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(tracked_players, f, ensure_ascii=False, indent=2)

def is_player_online(username):
    """Получает статус игрока в Roblox"""
    try:
        user_info = requests.get(
            f"https://users.roblox.com/v1/users/search?keyword={username}&limit=1",
            timeout=10
        ).json()

        if not user_info.get("data"):
            return None

        user_id = user_info["data"][0]["id"]

        presence = requests.post(
            "https://presence.roblox.com/v1/presence/users",
            json={"userIds": [user_id]},
            timeout=10
        ).json()

        if not presence.get("userPresences"):
            return None

        status = presence["userPresences"][0]["userPresenceType"]
        return status == 2  # 2 = в игре
    except Exception as e:
        print(f"Ошибка при проверке {username}: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "Привет! 👋 Это бот для автоматических нотификаций о статусе аккаунтов Roblox.\n\n"
        "📋 Команды:\n"
        "/add [Ник] - начать отслеживание аккаунта\n"
        "/stop [Ник/All] - остановить отслеживание\n"
        "/list - список отслеживаемых аккаунтов\n\n"
        "💡 Пример: /add MyNickname"
    )

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить аккаунт для отслеживания"""
    if not context.args:
        await update.message.reply_text("❌ Используй: /add [Ник]")
        return

    username = " ".join(context.args)  # Поддержка ников с пробелами
    chat_id = str(update.effective_chat.id)

    if chat_id not in tracked_players:
        tracked_players[chat_id] = {}
    
    if chat_id not in player_status:
        player_status[chat_id] = {}

    if username in tracked_players[chat_id]:
        await update.message.reply_text(f"⚠️ {username} уже отслеживается!")
        return

    # Проверяем, существует ли игрок
    online_status = is_player_online(username)
    if online_status is None:
        await update.message.reply_text(f"❌ Не удалось найти игрока: {username}")
        return

    # Добавляем с начальным сообщением
    tracked_players[chat_id][username] = None
    player_status[chat_id][username] = online_status
    save_data()

    status_text = "🟢 в игре" if online_status else "🔴 не в игре"
    msg = await update.message.reply_text(
        f"✅ Отслеживание включено!\n\n"
        f"👤 Игрок: {username}\n"
        f"📊 Статус: {status_text}"
    )
    
    # Сохраняем ID сообщения для редактирования
    tracked_players[chat_id][username] = msg.message_id
    save_data()

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Остановить отслеживание"""
    if not context.args:
        await update.message.reply_text("❌ Используй: /stop [Ник] или /stop all")
        return

    arg = " ".join(context.args).lower()
    chat_id = str(update.effective_chat.id)

    if chat_id not in tracked_players or not tracked_players[chat_id]:
        await update.message.reply_text("❌ Нет отслеживаемых аккаунтов!")
        return

    if arg == "all":
        tracked_players[chat_id] = {}
        if chat_id in player_status:
            player_status[chat_id] = {}
        save_data()
        await update.message.reply_text("⛔ Все аккаунты удалены из отслеживания")
    else:
        username = arg
        if username in tracked_players[chat_id]:
            del tracked_players[chat_id][username]
            if chat_id in player_status and username in player_status[chat_id]:
                del player_status[chat_id][username]
            save_data()
            await update.message.reply_text(f"⛔ Отслеживание остановлено: {username}")
        else:
            await update.message.reply_text(f"❌ Аккаунт не найден: {username}")

async def list_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список отслеживаемых аккаунтов"""
    chat_id = str(update.effective_chat.id)

    if chat_id not in tracked_players or not tracked_players[chat_id]:
        await update.message.reply_text("📋 Нет отслеживаемых аккаунтов")
        return

    message = "📋 Отслеживаемые аккаунты:\n\n"
    for username in tracked_players[chat_id]:
        status = player_status.get(chat_id, {}).get(username, False)
        status_emoji = "🟢" if status else "🔴"
        message += f"{status_emoji} {username}\n"

    await update.message.reply_text(message)

async def check_players(app):
    """Фоновая задача для проверки статусов"""
    print("🔄 Запуск фонового мониторинга...")
    await asyncio.sleep(5)  # Начальная задержка для инициализации
    
    while True:
        try:
            for chat_id, users in list(tracked_players.items()):
                for username in list(users.keys()):
                    try:
                        online = is_player_online(username)

                        if online is None:
                            continue

                        # Инициализируем статус для чата если его нет
                        if chat_id not in player_status:
                            player_status[chat_id] = {}

                        last_status = player_status[chat_id].get(username, False)

                        # Статус изменился
                        if online != last_status:
                            player_status[chat_id][username] = online
                            status_text = "🟢 зашел в игру!" if online else "🔴 вышел из игры!"

                            # Получаем ID сохраненного сообщения
                            message_id = tracked_players[chat_id][username]

                            try:
                                if message_id:
                                    # Редактируем существующее сообщение
                                    await app.bot.edit_message_text(
                                        chat_id=int(chat_id),
                                        message_id=message_id,
                                        text=f"👤 {username}\n{status_text}\n\n⏰ Обновлено: <code>{'🔴' if not online else '🟢'}</code>",
                                        parse_mode="HTML"
                                    )
                                else:
                                    # Отправляем новое сообщение
                                    msg = await app.bot.send_message(
                                        int(chat_id),
                                        f"👤 {username}\n{status_text}",
                                        parse_mode="HTML"
                                    )
                                    tracked_players[chat_id][username] = msg.message_id
                                    save_data()
                            except Exception as e:
                                print(f"Ошибка при отправке сообщения: {e}")
                                # Отправляем новое сообщение если редактирование не прошло
                                try:
                                    msg = await app.bot.send_message(
                                        int(chat_id),
                                        f"👤 {username}\n{status_text}\n\n⏰ Ошибка редактирования сообщения"
                                    )
                                    tracked_players[chat_id][username] = msg.message_id
                                    save_data()
                                except Exception as e2:
                                    print(f"Ошибка при отправке нового сообщения: {e2}")

                    except Exception as e:
                        print(f"Ошибка при проверке {username}: {e}")
                        continue

            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"Ошибка в check_players: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

async def main():
    """Главная функция"""
    load_data()

    app = ApplicationBuilder().token(TOKEN).build()

    # Обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("list", list_players))

    # Запускаем фоновую задачу
    asyncio.create_task(check_players(app))

    print("✅ Бот запущен!")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())