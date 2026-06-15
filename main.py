import os
import logging
import datetime
import asyncio
import random
import json
import re
import math
from telethon import TelegramClient, events, utils
from telethon.tl.types import InputMediaDice, MessageEntityMentionName
from telethon.tl.functions.messages import GetHistoryRequest
from flask import Flask
from threading import Thread
from collections import defaultdict

# Настройка логов
logging.basicConfig(level=logging.INFO)

api_id = os.environ.get('API_ID')
api_hash = os.environ.get('API_HASH')

auto_reply_enabled = False
data_file = 'userbot_data.json'

# Загрузка сохранённых данных
def load_data():
    global auto_reply_enabled
    if os.path.exists(data_file):
        with open(data_file, 'r') as f:
            data = json.load(f)
            auto_reply_enabled = data.get('auto_reply_enabled', False)

def save_data():
    with open(data_file, 'w') as f:
        json.dump({'auto_reply_enabled': auto_reply_enabled}, f)

load_data()

client = TelegramClient('my_userbot', int(api_id), api_hash)
app = Flask(__name__)

# Rate limiting для автоответчика
reply_cooldown = defaultdict(float)
reminders = []

@app.route('/')
def home():
    return "🤖 UserBot работает 24/7!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def safe_eval(expr: str):
    """Безопасный eval только для математических выражений"""
    expr = expr.strip()
    # Разрешённые символы и функции
    allowed_chars = set("0123456789+-*/().% ")
    allowed_funcs = {'sqrt': math.sqrt, 'sin': math.sin, 'cos': math.cos, 
                     'tan': math.tan, 'log': math.log, 'abs': abs, 'pow': pow}
    
    if not all(c in allowed_chars for c in expr):
        return None
    
    # Безопасное выполнение через ограниченный namespace
    namespace = {'__builtins__': {}, 'math': math}
    namespace.update(allowed_funcs)
    
    try:
        result = eval(expr, namespace, {})
        return round(result, 6) if isinstance(result, float) else result
    except:
        return None

async def send_reminder(chat_id, message, delay):
    """Фоновая отправка напоминания"""
    await asyncio.sleep(delay)
    await client.send_message(chat_id, f"⏰ НАПОМИНАНИЕ:\n{message}")

# --- КОМАНДЫ (35+) ---

@client.on(events.NewMessage(pattern='/start', from_users='me'))
async def start_cmd(e):
    await e.edit("🤖 UserBot активен! Введите /help для списка команд.")

@client.on(events.NewMessage(pattern='/sleep', from_users='me'))
async def sleep_cmd(e):
    global auto_reply_enabled
    auto_reply_enabled = True
    save_data()
    await e.edit('💤 Автоответчик ВКЛЮЧЕН.')

@client.on(events.NewMessage(pattern='/wake', from_users='me'))
async def wake_cmd(e):
    global auto_reply_enabled
    auto_reply_enabled = False
    save_data()
    await e.edit('☀️ Автоответчик ВЫКЛЮЧЕН.')

@client.on(events.NewMessage(pattern='/status', from_users='me'))
async def status_cmd(e):
    reply_status = "💤 Включен" if auto_reply_enabled else "☀️ Выключен"
    uptime = "Работает с момента запуска"
    await e.edit(f'📊 Статус бота:\n• Автоответчик: {reply_status}\n• {uptime}')

@client.on(events.NewMessage(pattern='/time', from_users='me'))
async def time_cmd(e):
    now = datetime.datetime.now()
    await e.edit(f'🕐 {now.strftime("%H:%M:%S")}\n📅 {now.strftime("%d.%m.%Y")}')

@client.on(events.NewMessage(pattern='/ping', from_users='me'))
async def ping_cmd(e):
    start = datetime.datetime.now()
    await e.edit("🏓 Понг...")
    end = datetime.datetime.now()
    ms = (end - start).microseconds / 1000
    await e.edit(f"🏓 Понг! {ms:.2f} мс")

@client.on(events.NewMessage(pattern='/id', from_users='me'))
async def id_cmd(e):
    chat = await e.get_chat()
    msg_id = e.reply_to_msg_id
    if msg_id:
        replied = await e.get_reply_message()
        sender_id = replied.sender_id
        await e.edit(f"🆔 ID чата: `{chat.id}`\n👤 ID отправителя: `{sender_id}`")
    else:
        await e.edit(f"🆔 ID чата: `{chat.id}`")

@client.on(events.NewMessage(pattern='/clear', from_users='me'))
async def clear_cmd(e):
    msg = await e.get_reply_message()
    if msg:
        await msg.delete()
        await e.delete()
    else:
        await e.edit("ℹ️ Ответьте на сообщение, которое хотите удалить")
        await asyncio.sleep(2)
        await e.delete()

@client.on(events.NewMessage(pattern='/info', from_users='me'))
async def info_cmd(e):
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    await e.edit(f"🚀 **UserBot Info**\n"
                 f"👤 Имя: {me.first_name}\n"
                 f"🆔 ID: `{me.id}`\n"
                 f"💬 Чатов: {len(dialogs)}\n"
                 f"📱 Версия: 2.0\n"
                 f"⚡ Статус: Активен")

@client.on(events.NewMessage(pattern='/restart', from_users='me'))
async def restart_cmd(e):
    await e.edit('🔄 Перезагрузка через 2 секунды...')
    await asyncio.sleep(2)
    await client.disconnect()
    os._exit(0)

@client.on(events.NewMessage(pattern='/help', from_users='me'))
async def help_cmd(e):
    help_text = """📚 **Доступные команды:**

**Основные:**
/sleep — Включить автоответчик
/wake — Выключить автоответчик
/status — Статус бота
/time — Текущее время
/ping — Проверка задержки
/id — ID чата/пользователя
/clear — Удалить сообщение (ответом)
/info — Информация о боте
/restart — Перезапуск
/help — Эта справка

**Пользователь:**
/me — Мой профиль
/avatar — Моя аватарка
/name [имя] — Сменить имя
/bio [текст] — Сменить био

**Игры и развлечения:**
/dice — Бросить кубик 🎲
/dart — Дротики 🎯
/basket — Баскетбол 🏀
/football — Футбол ⚽
/coin — Орел/решка
/rand [min] [max] — Случайное число
/8ball — Магический шар

**Утилиты:**
/calc [выражение] — Калькулятор
/remind [время] [текст] — Напоминание
/search [запрос] — Поиск в Google
/shorten [url] — Сократить ссылку
/weather [город] — Погода
/translate [текст] — Перевод (RU↔EN)

**Администрирование (осторожно):**
/purge [n] — Удалить n своих сообщений
/spam [n] [текст] — Отправить n сообщений
/echo [текст] — Повторить текст
/say [текст] — Сказать от имени бота

**Специальные:**
/save [ключ] [значение] — Сохранить текст
/get [ключ] — Получить сохранённое
/type [текст] — Эффект печати
/calc — Безопасный калькулятор"""
    await e.edit(help_text)

@client.on(events.NewMessage(pattern='/me', from_users='me'))
async def me_cmd(e):
    me = await client.get_me()
    await e.edit(f"👤 **{me.first_name}**\n"
                 f"🆔 ID: `{me.id}`\n"
                 f"🔰 @{me.username if me.username else 'Нет'}\n"
                 f"📱 {'Премиум' if me.premium else 'Обычный'} аккаунт")

@client.on(events.NewMessage(pattern='/avatar', from_users='me'))
async def avatar_cmd(e):
    me = await client.get_me()
    photos = await client.get_profile_photos(me.id, limit=1)
    if photos:
        await e.reply(file=photos[0])
        await e.delete()
    else:
        await e.edit("❌ У вас нет аватарки")

@client.on(events.NewMessage(pattern='/name', from_users='me'))
async def name_cmd(e):
    new_name = e.text.replace('/name ', '').strip()
    if new_name:
        await client.edit_profile(first_name=new_name)
        await e.edit(f"✅ Имя изменено на: {new_name}")
    else:
        await e.edit("ℹ️ Использование: /name [новое имя]")

@client.on(events.NewMessage(pattern='/bio', from_users='me'))
async def bio_cmd(e):
    new_bio = e.text.replace('/bio ', '').strip()
    if new_bio:
        await client.edit_profile(about=new_bio)
        await e.edit(f"✅ Био обновлено")
    else:
        await e.edit("ℹ️ Использование: /bio [новое био]")

@client.on(events.NewMessage(pattern='/dice', from_users='me'))
async def dice_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice(emoticon='🎲'))

@client.on(events.NewMessage(pattern='/dart', from_users='me'))
async def dart_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice(emoticon='🎯'))

@client.on(events.NewMessage(pattern='/basket', from_users='me'))
async def basket_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice(emoticon='🏀'))

@client.on(events.NewMessage(pattern='/football', from_users='me'))
async def football_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice(emoticon='⚽'))

@client.on(events.NewMessage(pattern='/coin', from_users='me'))
async def coin_cmd(e):
    result = random.choice(["Орёл 🦅", "Решка 💰"])
    await e.edit(f"🪙 Монета подброшена!\n\nРезультат: **{result}**")

@client.on(events.NewMessage(pattern='/8ball', from_users='me'))
async def eightball_cmd(e):
    answers = [
        "Определённо да ✅", "Скорее всего да 👍", "Хорошие перспективы 🌟",
        "Знаки говорят — да 🔮", "Да", "Пока не ясно, попробуй снова 🤔",
        "Спроси позже ⏰", "Лучше не рассказывать 🤫",
        "Нет ❌", "Мой ответ — нет 🚫", "Перспективы не очень 😕"
    ]
    await e.edit(f"🎱 Магический шар говорит:\n**{random.choice(answers)}**")

@client.on(events.NewMessage(pattern='/rand', from_users='me'))
async def rand_cmd(e):
    args = e.text.split()
    if len(args) >= 3:
        try:
            min_val = int(args[1])
            max_val = int(args[2])
            num = random.randint(min_val, max_val)
            await e.edit(f"🎲 Случайное число от {min_val} до {max_val}: **{num}**")
        except:
            await e.edit("❌ Использование: /rand [min] [max]")
    else:
        await e.edit(f"🎲 Случайное число: **{random.randint(1, 100)}**")

@client.on(events.NewMessage(pattern='/calc', from_users='me'))
async def calc_cmd(e):
    expr = e.text.replace('/calc', '').strip()
    if not expr:
        await e.edit("ℹ️ Использование: /calc 2+2*3")
        return
    
    result = await safe_eval(expr)
    if result is not None:
        await e.edit(f"🧮 {expr} = **{result}**")
    else:
        await e.edit("❌ Ошибка в выражении. Разрешены: + - * / ( ) % и числа")

@client.on(events.NewMessage(pattern='/remind', from_users='me'))
async def remind_cmd(e):
    args = e.text.split(maxsplit=2)
    if len(args) < 3:
        await e.edit("ℹ️ Использование: /remind [время в сек] [текст]\nПример: /remind 60 Позвонить маме")
        return
    
    try:
        delay = int(args[1])
        text = args[2]
        await e.edit(f"⏰ Напоминание установлено на {delay} секунд\n📝 {text}")
        asyncio.create_task(send_reminder(e.chat_id, text, delay))
    except ValueError:
        await e.edit("❌ Время должно быть числом (секунды)")

@client.on(events.NewMessage(pattern='/search', from_users='me'))
async def search_cmd(e):
    query = e.text.replace('/search', '').strip()
    if not query:
        await e.edit("ℹ️ Использование: /search [запрос]")
        return
    google_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
    await e.edit(f"🔍 **Поиск:** {query}\n\n🔗 [Открыть в Google]({google_url})")

@client.on(events.NewMessage(pattern='/shorten', from_users='me'))
async def shorten_cmd(e):
    import aiohttp
    url = e.text.replace('/shorten', '').strip()
    if not url:
        await e.edit("ℹ️ Использование: /shorten [URL]")
        return
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://tinyurl.com/api-create.php?url={url}") as resp:
                short = await resp.text()
                await e.edit(f"🔗 Сокращённая ссылка:\n{short.strip()}")
    except:
        await e.edit("❌ Ошибка при сокращении ссылки")

@client.on(events.NewMessage(pattern='/weather', from_users='me'))
async def weather_cmd(e):
    city = e.text.replace('/weather', '').strip()
    if not city:
        await e.edit("ℹ️ Использование: /weather [город]")
        return
    
    await e.edit(f"🌤️ Погода для {city}:\n" +
                 "ℹ️ Для точного прогноза используйте [OpenWeatherMap](https://openweathermap.org/city)")

@client.on(events.NewMessage(pattern='/translate', from_users='me'))
async def translate_cmd(e):
    text = e.text.replace('/translate', '').strip()
    if not text:
        await e.edit("ℹ️ Использование: /translate [текст для перевода]")
        return
    
    google_translate = f"https://translate.google.com/?sl=auto&tl=ru&text={text.replace(' ', '%20')}"
    await e.edit(f"🌐 Перевод:\n\n{text}\n\n🔗 [Открыть в Google Translate]({google_translate})")

@client.on(events.NewMessage(pattern='/spam', from_users='me'))
async def spam_cmd(e):
    args = e.text.split(maxsplit=2)
    if len(args) < 3:
        await e.edit("⚠️ Использование: /spam [количество] [текст]\nМаксимум: 20")
        return
    
    try:
        count = min(int(args[1]), 20)
        text = args[2]
        await e.delete()
        for i in range(count):
            await client.send_message(e.chat_id, f"🤖 {text}")
            await asyncio.sleep(0.5)
    except:
        await e.edit("❌ Ошибка в параметрах")

@client.on(events.NewMessage(pattern='/echo', from_users='me'))
async def echo_cmd(e):
    text = e.text.replace('/echo', '').strip()
    if text:
        await e.delete()
        await client.send_message(e.chat_id, text)

@client.on(events.NewMessage(pattern='/say', from_users='me'))
async def say_cmd(e):
    text = e.text.replace('/say', '').strip()
    if text:
        await e.delete()
        await client.send_message(e.chat_id, text)

@client.on(events.NewMessage(pattern='/type', from_users='me'))
async def type_cmd(e):
    text = e.text.replace('/type', '').strip()
    if not text:
        await e.edit("ℹ️ Использование: /type [текст]")
        return
    
    msg = await e.edit("█")
    for i, char in enumerate(text):
        current = text[:i+1] + "█"
        await msg.edit(current)
        await asyncio.sleep(0.05)
    await msg.edit(text)

@client.on(events.NewMessage(pattern='/purge', from_users='me'))
async def purge_cmd(e):
    args = e.text.split()
    limit = int(args[1]) if len(args) > 1 else 10
    limit = min(limit, 50)  # Максимум 50 сообщений
    
    my_id = (await client.get_me()).id
    await e.delete()
    
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        if msg.out or (msg.from_id and getattr(msg.from_id, 'user_id', None) == my_id):
            await msg.delete()
            count += 1
            await asyncio.sleep(0.5)
    
    info = await client.send_message(e.chat_id, f"✅ Удалено {count} сообщений")
    await asyncio.sleep(2)
    await info.delete()

@client.on(events.NewMessage(pattern='/save', from_users='me'))
async def save_cmd(e):
    args = e.text.split(maxsplit=2)
    if len(args) < 3:
        await e.edit("ℹ️ Использование: /save [ключ] [значение]")
        return
    
    key = args[1]
    value = args[2]
    saved = {}
    if os.path.exists('saved_data.json'):
        with open('saved_data.json', 'r') as f:
            saved = json.load(f)
    
    saved[key] = value
    with open('saved_data.json', 'w') as f:
        json.dump(saved, f)
    
    await e.edit(f"✅ Сохранено: {key} = {value}")

@client.on(events.NewMessage(pattern='/get', from_users='me'))
async def get_cmd(e):
    key = e.text.replace('/get', '').strip()
    if not key:
        await e.edit("ℹ️ Использование: /get [ключ]")
        return
    
    if os.path.exists('saved_data.json'):
        with open('saved_data.json', 'r') as f:
            saved = json.load(f)
        
        value = saved.get(key)
        if value:
            await e.edit(f"📦 {key} = {value}")
        else:
            await e.edit(f"❌ Ключ '{key}' не найден")
    else:
        await e.edit("❌ Нет сохранённых данных")

# --- АВТООТВЕТЧИК (с защитой от спама) ---
@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def auto_reply_handler(event):
    global auto_reply_enabled
    
    if not auto_reply_enabled:
        return
    
    sender = await event.get_sender()
    if not sender or sender.bot:
        return
    
    user_id = event.sender_id
    now = datetime.datetime.now().timestamp()
    
    # Защита от флуда: не чаще 1 раза в 10 секунд на пользователя
    if now - reply_cooldown[user_id] < 10:
        return
    
    reply_cooldown[user_id] = now
    await asyncio.sleep(1)  # Минимальная задержка
    await event.reply('💫 Я автоответчик, хозяин скоро ответит! Спасибо за терпение 😘')

if __name__ == "__main__":
    print("🚀 Запуск UserBot...")
    Thread(target=run_web).start()
    client.start()
    print("✅ UserBot запущен и готов к работе!")
    client.run_until_disconnected()
