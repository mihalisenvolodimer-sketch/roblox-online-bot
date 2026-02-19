import os
import asyncio
import time
import json
import io
import redis.asyncio as redis
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont, ImageOps

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = None

# Кэш для аватарок, чтобы не качать их каждую секунду
avatar_cache = {}

# --- Утилиты ---
def safe_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def format_duration(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    res = ""
    if h > 0: res += f"{h}ч "
    if m > 0: res += f"{m}м "
    res += f"{s}с"
    return res

async def get_roblox_avatar(username):
    """Получает аватарку игрока через Roblox API"""
    if username in avatar_cache:
        return avatar_cache[username]
    
    try:
        async with aiohttp.ClientSession() as session:
            # 1. Получаем UserID по нику
            post_data = {"usernames": [username], "excludeBannedUsers": True}
            async with session.post("https://users.roblox.com/v1/usernames/users", json=post_data) as resp:
                user_data = await resp.json()
                if not user_data.get("data"): return None
                user_id = user_data["data"][0]["id"]

            # 2. Получаем ссылку на голову (Thumbnail)
            thumb_url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=150x150&format=Png&isCircular=true"
            async with session.get(thumb_url) as resp:
                thumb_data = await resp.json()
                img_url = thumb_data["data"][0]["imageUrl"]
            
            # 3. Скачиваем саму картинку
            async with session.get(img_url) as resp:
                img_bytes = await resp.read()
                avatar_cache[username] = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                return avatar_cache[username]
    except:
        return None

# --- Команды ---

@dp.message(Command("img_create"))
async def create_image_status(message: types.Message):
    if not accounts: return await message.answer("Нет активных аккаунтов в мониторинге.")
    
    wait_msg = await message.answer("⏳ Генерирую отчет с аватарками...")
    
    try:
        width, height = 700, 150 + (len(accounts) * 60)
        img = Image.new('RGB', (width, height), color=(20, 20, 20))
        draw = ImageDraw.Draw(img)
        
        try:
            font_main = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
        except:
            font_main = ImageFont.load_default()
            font_small = ImageFont.load_default()

        draw.text((40, 30), "📊 ROBLOX LIVE MONITORING", fill=(255, 255, 255), font=font_main)
        draw.text((40, 65), f"Дата: {time.strftime('%d.%m.%Y %H:%M:%S')}", fill=(150, 150, 150), font=font_small)
        draw.line((40, 100, 660, 100), fill=(50, 50, 50), width=2)

        y, now = 120, time.time()
        for user in sorted(accounts.keys()):
            is_online = now - accounts[user] < 120
            # Фон плашки: зеленый если онлайн, серый если оффлайн
            bg_color = (45, 80, 45) if is_online else (40, 40, 40)
            draw.rounded_rectangle([40, y, 660, y+50], radius=8, fill=bg_color)

            # Пытаемся получить аватарку
            avatar = await get_roblox_avatar(user)
            if avatar:
                avatar_resized = avatar.resize((40, 40), Image.LANCZOS)
                img.paste(avatar_resized, (50, y+5), avatar_resized)
            else:
                # Если нет аватарки - рисуем кружок
                dot_color = (0, 255, 0) if is_online else (255, 0, 0)
                draw.ellipse((50, y+10, 80, y+40), fill=dot_color)

            # Никнейм
            draw.text((105, y+12), f"{user}", fill=(255, 255, 255), font=font_small)
            
            # Время
            status_text = "OFFLINE"
            if is_online and user in start_times:
                status_text = f"Online: {format_duration(now - start_times[user])}"
            
            # Рисуем время справа
            draw.text((450, y+12), status_text, fill=(220, 220, 220), font=font_small)
            
            y += 60

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        
        await wait_msg.delete()
        await message.answer_photo(
            BufferedInputFile(buf.read(), filename="report.png"), 
            caption=f"✅ Отчет по {len(accounts)} аккаунтам готов."
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# --- ОСТАЛЬНОЙ КОД БОТА (init_db, save_to_db, add, disable, и т.д. берем из прошлых сообщений) ---
# ... (вставь сюда свои функции /add, /list, /disable, update_status_message из прошлого кода)
