import requests
import asyncio
import json
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TOKEN")
CHECK_INTERVAL = 60
DATA_FILE = "players.json"

tracked_players = {}
player_status = {}

def load_data():
    global tracked_players
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            tracked_players.update(json.load(f))

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(tracked_players, f)

def is_player_online(username):
    try:
        user_info = requests.get(
            f"https://users.roblox.com/v1/users/search?keyword={username}&limit=1"
        ).json()

        if not user_info["data"]:
            return None

        user_id = user_info["data"][0]["id"]

        presence = requests.post(
            "https://presence.roblox.com/v1/presence/users",
            json={"userIds": [user_id]}
        ).json()

        status = presence["userPresences"][0]["userPresenceType"]

        return status == 2
    except:
        return None

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Используй: /add Ник")
        return

    username = context.args[0]
    chat_id = str(update.effective_chat.id)

    if chat_id not in tracked_players:
        tracked_players[chat_id] = []

    if username not in tracked_players[chat_id]:
        tracked_players[chat_id].append(username)
        save_data()

    await update.message.reply_text(f"✅ Отслеживание включено: {username}")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Используй: /stop Ник или /stop all")
        return

    arg = context.args[0]
    chat_id = str(update.effective_chat.id)

    if chat_id not in tracked_players:
        return

    if arg.lower() == "all":
        tracked_players[chat_id] = []
        save_data()
        await update.message.reply_text("⛔ Всё остановлено")
    else:
        if arg in tracked_players[chat_id]:
            tracked_players[chat_id].remove(arg)
            save_data()
            await update.message.reply_text(f"⛔ Остановлено: {arg}")

async def check_players(app):
    while True:
        for chat_id, users in tracked_players.items():
            for username in users:
                online = is_player_online(username)

                if online is None:
                    continue

                last_status = player_status.get(username, False)

                if online and not last_status:
                    await app.bot.send_message(chat_id, f"🟢 {username} зашел в игру!")

                elif not online and last_status:
                    await app.bot.send_message(chat_id, f"🔴 {username} вышел!")

                player_status[username] = online

        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    load_data()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("stop", stop))

    asyncio.create_task(check_players(app))

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
