import requests
import asyncio
import json
import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    logger.error("TOKEN not set!")
    exit(1)

CHECK_INTERVAL = 30
DATA_FILE = "players.json"

tracked_players = {}
player_status = {}

def load_data():
    global tracked_players
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                tracked_players = json.load(f)
                logger.info("Data loaded")
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            tracked_players = {}
    else:
        tracked_players = {}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(tracked_players, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def is_player_online(username):
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
        return status == 2
    except Exception as e:
        logger.debug(f"Error checking {username}: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 👋 Это бот для автоматических нотификаций о статусе аккаунтов Roblox.\n\n"
        "📋 Команды:\n"
        "/add [Ник] - начать отслеживание аккаунта\n"
        "/stop [Ник/All] - остановить отслеживание\n"
        "/list - список отслеживаемых аккаунтов\n\n"
        "💡 Пример: /add MyNickname"
    )

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Используй: /add [Ник]")
        return

    username = " ".join(context.args)
    chat_id = str(update.effective_chat.id)

    if chat_id not in tracked_players:
        tracked_players[chat_id] = {}
    
    if chat_id not in player_status:
        player_status[chat_id] = {}

    if username in tracked_players[chat_id]:
        await update.message.reply_text(f"⚠️ {username} уже отслеживается!")
        return

    online_status = is_player_online(username)
    if online_status is None:
        await update.message.reply_text(f"❌ Не удалось найти игрока: {username}")
        return

    tracked_players[chat_id][username] = None
    player_status[chat_id][username] = online_status
    save_data()

    status_text = "🟢 в игре" if online_status else "🔴 не в игре"
    msg = await update.message.reply_text(
        f"✅ Отслеживание включено!\n\n"
        f"👤 Игрок: {username}\n"
        f"📊 Статус: {status_text}"
    )
    
    tracked_players[chat_id][username] = msg.message_id
    save_data()

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def check_players(context):
    app = context.bot._bot.get_bot()
    try:
        for chat_id, users in list(tracked_players.items()):
            for username in list(users.keys()):
                try:
                    online = is_player_online(username)

                    if online is None:
                        continue

                    if chat_id not in player_status:
                        player_status[chat_id] = {}

                    last_status = player_status[chat_id].get(username, False)

                    if online != last_status:
                        player_status[chat_id][username] = online
                        status_text = "🟢 зашел в игру!" if online else "🔴 вышел из игры!"
                        message_id = tracked_players[chat_id][username]

                        try:
                            if message_id:
                                await app.bot.edit_message_text(
                                    chat_id=int(chat_id),
                                    message_id=message_id,
                                    text=f"👤 {username}\n{status_text}",
                                    parse_mode="HTML"
                                )
                            else:
                                msg = await app.bot.send_message(
                                    int(chat_id),
                                    f"👤 {username}\n{status_text}",
                                    parse_mode="HTML"
                                )
                                tracked_players[chat_id][username] = msg.message_id
                                save_data()
                        except Exception as e:
                            logger.error(f"Error sending: {e}")

                except Exception as e:
                    logger.error(f"Error: {e}")
                    continue
    except Exception as e:
        logger.error(f"Error in check_players: {e}")

async def main():
    logger.info("Starting bot...")
    load_data()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("list", list_players))

    app.job_queue.run_repeating(check_players, interval=CHECK_INTERVAL, first=5)

    logger.info("Bot ready!")
    await app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")