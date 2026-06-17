import os
import logging
import datetime
import asyncio
import random
import json
import re
import math
import time
import hashlib
import base64
import string
from telethon import TelegramClient, events, utils
from telethon.tl.types import InputMediaDice, MessageEntityMentionName
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.functions.account import UpdateProfileRequest
from flask import Flask
from threading import Thread
from collections import defaultdict

# Настройка логов
logging.basicConfig(level=logging.INFO)

api_id = os.environ.get('API_ID')
api_hash = os.environ.get('API_HASH')

auto_reply_enabled = False
auto_reply_text = '💫 Я автоответчик, хозяин скоро ответит! Спасибо за терпение 😘'
data_file = 'userbot_data.json'
saved_file = 'saved_data.json'
notes_file = 'notes_data.json'
afk_start_time = None
afk_reason = ""

# Загрузка сохранённых данных
def load_data():
    global auto_reply_enabled, auto_reply_text
    if os.path.exists(data_file):
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            auto_reply_enabled = data.get('auto_reply_enabled', False)
            auto_reply_text = data.get('auto_reply_text', auto_reply_text)

def save_data():
    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump({'auto_reply_enabled': auto_reply_enabled, 'auto_reply_text': auto_reply_text}, f, ensure_ascii=False)

def load_saved():
    if os.path.exists(saved_file):
        with open(saved_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def write_saved(data):
    with open(saved_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)

def load_notes():
    if os.path.exists(notes_file):
        with open(notes_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def write_notes(data):
    with open(notes_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)

load_data()

# Исправление для Python 3.14
try:
    client = TelegramClient('my_userbot', int(api_id), api_hash)
except ValueError as e:
    if "too many values to unpack" in str(e):
        from telethon.sessions import SQLiteSession
        session = SQLiteSession('my_userbot')
        client = TelegramClient(session, int(api_id), api_hash)
    else:
        raise

app = Flask(__name__)

# Rate limiting
reply_cooldown = defaultdict(float)
cmd_cooldown = defaultdict(float)

@app.route('/')
def home():
    return "🤖 UserBot работает 24/7!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# ══════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════════════

async def safe_eval(expr: str):
    """Безопасный eval для математических выражений"""
    expr = expr.strip()
    # Разрешаем буквенные функции (sin, cos и т.д.) и числа
    allowed_pattern = r'^[\d\s\+\-\*\/\(\)\.\%\^]+$'
    func_aliases = {
        'sqrt': 'math.sqrt', 'sin': 'math.sin', 'cos': 'math.cos',
        'tan': 'math.tan', 'log': 'math.log', 'log2': 'math.log2',
        'log10': 'math.log10', 'abs': 'abs', 'pow': 'pow',
        'floor': 'math.floor', 'ceil': 'math.ceil',
        'pi': 'math.pi', 'e': 'math.e', 'round': 'round',
        'factorial': 'math.factorial', 'gcd': 'math.gcd',
    }
    safe_expr = expr
    for k, v in func_aliases.items():
        safe_expr = re.sub(rf'\b{k}\b', v, safe_expr)

    # Запрещаем опасные вещи
    if any(w in safe_expr for w in ['import', 'os', 'sys', 'open', 'exec', 'eval', '__']):
        return None

    namespace = {'__builtins__': {}, 'math': math, 'abs': abs, 'pow': pow, 'round': round}
    try:
        result = eval(safe_expr, namespace, {})
        if isinstance(result, float):
            if math.isinf(result) or math.isnan(result):
                return "∞ / NaN"
            return round(result, 10)
        return result
    except:
        return None

async def send_reminder(chat_id, message, delay):
    await asyncio.sleep(delay)
    await client.send_message(chat_id, f"⏰ **НАПОМИНАНИЕ:**\n{message}")

def fmt_seconds(s):
    """Форматирует секунды в удобочитаемый вид"""
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if h: parts.append(f"{h}ч")
    if m: parts.append(f"{m}м")
    if sec or not parts: parts.append(f"{sec}с")
    return " ".join(parts)

def caesar_cipher(text, shift, decrypt=False):
    if decrypt: shift = -shift
    result = []
    for c in text:
        if c.isalpha():
            base = ord('А') if 'А' <= c <= 'я' or c in 'ёЁ' else ord('A')
            size = 33 if base == ord('А') else 26
            result.append(chr((ord(c) - base + shift) % size + base))
        else:
            result.append(c)
    return ''.join(result)

def morse_encode(text):
    MORSE = {
        'A':'.-','B':'-...','C':'-.-.','D':'-..','E':'.','F':'..-.','G':'--.','H':'....','I':'..','J':'.---',
        'K':'-.-','L':'.-..','M':'--','N':'-.','O':'---','P':'.--.','Q':'--.-','R':'.-.','S':'...','T':'-',
        'U':'..-','V':'...-','W':'.--','X':'-..-','Y':'-.--','Z':'--..',
        '0':'-----','1':'.----','2':'..---','3':'...--','4':'....-','5':'.....','6':'-....','7':'--...','8':'---..','9':'----.',
        ' ': '/'
    }
    return ' '.join(MORSE.get(c.upper(), '?') for c in text)

def generate_password(length=16, use_symbols=True):
    chars = string.ascii_letters + string.digits
    if use_symbols:
        chars += "!@#$%^&*()-_=+[]{}|;:,.<>?"
    return ''.join(random.SystemRandom().choice(chars) for _ in range(length))

# ══════════════════════════════════════════════════
#  ОСНОВНЫЕ КОМАНДЫ
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/sleep', from_users='me'))
async def sleep_cmd(e):
    global auto_reply_enabled
    auto_reply_enabled = True
    save_data()
    await e.edit('💤 Автоответчик **ВКЛЮЧЕН**.')

@client.on(events.NewMessage(pattern=r'/wake', from_users='me'))
async def wake_cmd(e):
    global auto_reply_enabled
    auto_reply_enabled = False
    save_data()
    await e.edit('☀️ Автоответчик **ВЫКЛЮЧЕН**.')

@client.on(events.NewMessage(pattern=r'/setreply (.+)', from_users='me'))
async def setreply_cmd(e):
    global auto_reply_text
    auto_reply_text = e.pattern_match.group(1).strip()
    save_data()
    await e.edit(f"✅ Текст автоответчика обновлён:\n\n_{auto_reply_text}_")

@client.on(events.NewMessage(pattern=r'/status', from_users='me'))
async def status_cmd(e):
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    reply_status = "💤 Включен" if auto_reply_enabled else "☀️ Выключен"
    uptime = fmt_seconds(time.time() - bot_start_time)
    afk_status = f"✅ AFK ({afk_reason or 'без причины'})" if afk_start_time else "❌ Нет"
    await e.edit(
        f"📊 **Статус UserBot**\n\n"
        f"👤 Аккаунт: {me.first_name}\n"
        f"💬 Чатов: {len(dialogs)}\n"
        f"🤖 Автоответчик: {reply_status}\n"
        f"😴 AFK: {afk_status}\n"
        f"⏱ Аптайм: {uptime}"
    )

@client.on(events.NewMessage(pattern=r'/time', from_users='me'))
async def time_cmd(e):
    now = datetime.datetime.now()
    utc = datetime.datetime.utcnow()
    await e.edit(
        f"🕐 **Время**\n\n"
        f"🏠 Локальное: `{now.strftime('%H:%M:%S')}`\n"
        f"🌍 UTC: `{utc.strftime('%H:%M:%S')}`\n"
        f"📅 Дата: `{now.strftime('%d.%m.%Y')}`\n"
        f"📆 День: `{now.strftime('%A')}`"
    )

@client.on(events.NewMessage(pattern=r'/ping', from_users='me'))
async def ping_cmd(e):
    start = time.monotonic()
    await e.edit("🏓 Тестирую...")
    ms = (time.monotonic() - start) * 1000
    quality = "🟢" if ms < 200 else "🟡" if ms < 500 else "🔴"
    await e.edit(f"🏓 **Понг!** {quality}\n⚡ Задержка: `{ms:.2f} мс`")

@client.on(events.NewMessage(pattern=r'/id', from_users='me'))
async def id_cmd(e):
    chat = await e.get_chat()
    lines = [f"🆔 **ID чата:** `{chat.id}`"]
    if e.reply_to_msg_id:
        replied = await e.get_reply_message()
        lines.append(f"👤 **ID отправителя:** `{replied.sender_id}`")
        lines.append(f"📨 **ID сообщения:** `{replied.id}`")
        if hasattr(replied.sender, 'username') and replied.sender.username:
            lines.append(f"🔖 **Username:** @{replied.sender.username}")
    else:
        me = await client.get_me()
        lines.append(f"👤 **Мой ID:** `{me.id}`")
    await e.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'/info', from_users='me'))
async def info_cmd(e):
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    uptime = fmt_seconds(time.time() - bot_start_time)
    await e.edit(
        f"🚀 **UserBot Info**\n\n"
        f"👤 Имя: **{me.first_name}** {me.last_name or ''}\n"
        f"🆔 ID: `{me.id}`\n"
        f"🔰 Username: @{me.username or 'нет'}\n"
        f"📱 Телефон: `{me.phone or 'скрыт'}`\n"
        f"💬 Чатов: `{len(dialogs)}`\n"
        f"⏱ Аптайм: `{uptime}`\n"
        f"⚡ Статус: **Активен**"
    )

@client.on(events.NewMessage(pattern=r'/restart', from_users='me'))
async def restart_cmd(e):
    await e.edit('🔄 Перезагрузка через 2 секунды...')
    await asyncio.sleep(2)
    await client.disconnect()
    os._exit(0)

@client.on(events.NewMessage(pattern=r'/help(?:\s+(.+))?', from_users='me'))
async def help_cmd(e):
    cat = (e.pattern_match.group(1) or '').strip().lower()

    cats = {
        'основные': (
            "⚙️ **ОСНОВНЫЕ КОМАНДЫ**\n\n"
            "`/sleep` — Включить автоответчик. Бот будет автоматически отвечать всем кто пишет в личку.\n\n"
            "`/wake` — Выключить автоответчик.\n\n"
            "`/setreply [текст]` — Задать свой текст автоответчика.\n"
            "   Пример: `/setreply Я занят, перезвоню позже`\n\n"
            "`/status` — Показать текущий статус бота: аптайм, автоответчик, AFK, количество чатов.\n\n"
            "`/time` — Показать текущее время (локальное и UTC) и дату.\n\n"
            "`/ping` — Проверить задержку соединения с Telegram в миллисекундах.\n\n"
            "`/id` — Показать ID текущего чата и свой ID.\n"
            "   Ответом на сообщение: покажет ID отправителя, сообщения и username.\n\n"
            "`/info` — Подробная информация о боте: имя, ID, username, телефон, аптайм.\n\n"
            "`/restart` — Перезапустить бота (через 2 секунды).\n\n"
            "`/help [категория]` — Эта справка. Категории: основные, профиль, игры, утилиты, сообщения, заметки, afk, инфо"
        ),
        'профиль': (
            "👤 **ПРОФИЛЬ**\n\n"
            "`/me` — Показать свой профиль: имя, ID, username, телефон, наличие аватарки.\n\n"
            "`/avatar` — Отправить свою аватарку в чат.\n"
            "   Ответом на сообщение: отправит аватарку того пользователя.\n\n"
            "`/name [имя]` — Сменить имя в профиле Telegram.\n"
            "   Пример: `/name Алексей`\n\n"
            "`/lastname [фамилия]` — Сменить фамилию в профиле.\n"
            "   Без аргумента `/lastname` — удалит фамилию.\n\n"
            "`/bio [текст]` — Обновить описание профиля (о себе).\n"
            "   Без аргумента `/bio` — очистит описание.\n\n"
            "`/username_check @ник` — Проверить, занят ли username в Telegram.\n"
            "   Если занят — покажет имя и ID владельца."
        ),
        'игры': (
            "🎮 **ИГРЫ И РАЗВЛЕЧЕНИЯ**\n\n"
            "`/dice` — Бросить кубик 🎲 (анимация Telegram).\n\n"
            "`/dart` — Бросить дротик 🎯 (анимация Telegram).\n\n"
            "`/basket` — Баскетбольный бросок 🏀 (анимация Telegram).\n\n"
            "`/football` — Удар по мячу ⚽ (анимация Telegram).\n\n"
            "`/coin` — Подбросить монету. Орёл или решка.\n\n"
            "`/rand` — Случайное число от 1 до 100.\n"
            "`/rand [макс]` — от 1 до макс.\n"
            "`/rand [мин] [макс]` — в заданном диапазоне (поддерживает отрицательные).\n\n"
            "`/8ball [вопрос]` — Магический шар 🎱. Задай вопрос — получи ответ с анимацией, комментарием и процентом уверенности. 25 уникальных ответов.\n\n"
            "`/rps [к/н/б]` — Камень, ножницы, бумага против бота.\n"
            "   Пример: `/rps камень` или `/rps к`\n\n"
            "`/slot` — Слот-машина 🎰. Три символа — пробуй поймать джекпот.\n\n"
            "`/lucky` — Индекс удачи на сегодня с прогресс-баром и советом."
        ),
        'утилиты': (
            "🛠 **УТИЛИТЫ**\n\n"
            "`/calc [выражение]` — Калькулятор. Поддерживает: `+ - * / % ( )`, а также `sqrt sin cos tan log log2 log10 abs pow floor ceil round factorial gcd pi e`.\n"
            "   Пример: `/calc sqrt(144) + pi * 2`\n\n"
            "`/remind [секунды] [текст]` — Напоминание через N секунд. Без лимита времени.\n"
            "   Пример: `/remind 3600 Позвонить маме`\n\n"
            "`/search [запрос]` — Поиск в Google, DuckDuckGo и YouTube. Возвращает ссылки.\n\n"
            "`/shorten [url]` — Сократить ссылку через TinyURL.\n\n"
            "`/weather [город]` — Ссылки на прогноз погоды: wttr.in, OpenWeatherMap, Weather.com.\n\n"
            "`/translate [текст]` — Ссылки на Google Translate в трёх направлениях: RU→EN, EN→RU, Auto→RU.\n\n"
            "`/base64 encode [текст]` — Закодировать текст в Base64.\n"
            "`/base64 decode [текст]` — Декодировать из Base64.\n\n"
            "`/hash [текст]` — Вычислить хэши: MD5, SHA1, SHA256.\n\n"
            "`/morse [текст]` — Перевести текст в азбуку Морзе.\n\n"
            "`/caesar encode [сдвиг] [текст]` — Зашифровать шифром Цезаря.\n"
            "`/caesar decode [сдвиг] [текст]` — Расшифровать. Работает с русским и английским.\n"
            "   Пример: `/caesar encode 3 Привет`\n\n"
            "`/password` — Сгенерировать пароль (16 симв. с символами).\n"
            "`/password [длина]` — Задать длину (4–128).\n"
            "`/password [длина] simple` — Без спецсимволов.\n\n"
            "`/qr [текст или ссылка]` — QR-код: ссылка на изображение 300×300."
        ),
        'сообщения': (
            "✉️ **УПРАВЛЕНИЕ СООБЩЕНИЯМИ**\n\n"
            "`/type [текст]` — Эффект живой печати: текст появляется посимвольно с курсором.\n\n"
            "`/echo [текст]` — Удалить свою команду и отправить чистый текст от своего имени.\n\n"
            "`/say [текст]` — То же что echo: отправить текст без следа команды.\n\n"
            "`/clean [n]` — Удалить свои последние N сообщений в чате. По умолчанию 10. Без лимита.\n\n"
            "`/purge [n]` — ⚠️ Удалить ЛЮБЫЕ последние N сообщений (свои и чужие). Без ограничений.\n\n"
            "`/spam [n] [текст]` — Отправить текст N раз подряд. Без ограничений на количество.\n"
            "   Пример: `/spam 5 Привет!`\n\n"
            "`/forward [chat_id]` — Переслать сообщение (ответом) в другой чат по его ID.\n\n"
            "`/pin` — Закрепить сообщение (ответом) без уведомления участников.\n\n"
            "`/unpin` — Открепить сообщение (ответом) или последнее закреплённое.\n\n"
            "`/copyall [n] [chat_id]` — Скопировать N последних сообщений текущего чата в другой чат.\n"
            "   Пример: `/copyall 20 -1001234567890`"
        ),
        'заметки': (
            "📦 **ЗАМЕТКИ И ХРАНИЛИЩЕ**\n\n"
            "— **Быстрое хранилище** (ключ→значение):\n\n"
            "`/save [ключ] [значение]` — Сохранить любой текст под ключом.\n"
            "   Пример: `/save адрес ул. Ленина 5, кв. 12`\n\n"
            "`/get [ключ]` — Получить сохранённое значение по ключу.\n\n"
            "`/del [ключ]` — Удалить запись по ключу.\n\n"
            "`/list` — Показать все сохранённые ключи и первые 40 символов значений.\n\n"
            "— **Заметки** (длинные тексты):\n\n"
            "`/note [название] [текст]` — Сохранить заметку. Можно ответом на сообщение — текст возьмётся из него.\n\n"
            "`/getnote [название]` — Показать содержимое заметки полностью.\n\n"
            "`/delnote [название]` — Удалить заметку.\n\n"
            "`/notes` — Список всех заметок с превью текста."
        ),
        'afk': (
            "😴 **AFK — режим отсутствия**\n\n"
            "`/afk` — Включить режим AFK. Все кто напишут в личку получат автоуведомление с временем отсутствия.\n\n"
            "`/afk [причина]` — AFK с указанием причины.\n"
            "   Пример: `/afk сплю до 10 утра`\n\n"
            "`/unafk` — Выключить AFK. Покажет сколько времени ты отсутствовал.\n\n"
            "ℹ️ Каждый пользователь получает уведомление не чаще раза в 60 секунд.\n"
            "ℹ️ AFK и автоответчик работают независимо друг от друга."
        ),
        'инфо': (
            "📊 **ИНФОРМАЦИЯ О ЧАТЕ**\n\n"
            "`/chatinfo` — Показать информацию о текущем чате: название, ID, username, тип, количество участников.\n\n"
            "`/members` — Подсчитать участников чата: всего, людей и ботов.\n\n"
            "`/admins` — Список всех администраторов чата с username или ID.\n\n"
            "`/top [n]` — Топ-10 самых активных участников по количеству сообщений.\n"
            "   N — сколько последних сообщений анализировать (по умолчанию 100).\n"
            "   Пример: `/top 500`"
        ),
    }

    if cat and cat in cats:
        await e.edit(cats[cat])
        return
    elif cat and cat not in cats:
        await e.edit(f"❌ Категория `{cat}` не найдена.\n\nДоступные: `{', '.join(cats.keys())}`")
        return

    help_text = (
        "📚 **UserBot — 50 команд**\n\n"
        "Используй `/help [категория]` для подробного описания:\n\n"
        "⚙️ `/help основные` — sleep, wake, setreply, status, time, ping, id, info, restart\n\n"
        "👤 `/help профиль` — me, avatar, name, bio, lastname, username_check\n\n"
        "🎮 `/help игры` — dice, dart, basket, football, coin, rand, 8ball, rps, slot, lucky\n\n"
        "🛠 `/help утилиты` — calc, remind, search, shorten, weather, translate, base64, hash, morse, caesar, password, qr\n\n"
        "✉️ `/help сообщения` — type, echo, say, clean, purge, spam, forward, pin, unpin, copyall\n\n"
        "📦 `/help заметки` — save, get, del, list, note, getnote, delnote, notes\n\n"
        "😴 `/help afk` — afk, unafk\n\n"
        "📊 `/help инфо` — chatinfo, members, admins, top\n\n"
        "⚠️ `/purge` и `/spam` работают без каких-либо ограничений!"
    )
    await e.edit(help_text)

# ══════════════════════════════════════════════════
#  ПРОФИЛЬ
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/me', from_users='me'))
async def me_cmd(e):
    me = await client.get_me()
    photos = await client.get_profile_photos(me.id, limit=1)
    has_photo = "✅" if photos else "❌"
    await e.edit(
        f"👤 **Мой профиль**\n\n"
        f"📛 Имя: **{me.first_name}** {me.last_name or ''}\n"
        f"🆔 ID: `{me.id}`\n"
        f"🔰 Username: @{me.username or 'нет'}\n"
        f"📱 Телефон: `{me.phone or 'скрыт'}`\n"
        f"🖼 Аватар: {has_photo}\n"
        f"🤖 Бот: {'Да' if me.bot else 'Нет'}\n"
        f"✔️ Verified: {'Да' if me.verified else 'Нет'}"
    )

@client.on(events.NewMessage(pattern=r'/avatar', from_users='me'))
async def avatar_cmd(e):
    target = None
    if e.reply_to_msg_id:
        replied = await e.get_reply_message()
        target = replied.sender_id
    else:
        me = await client.get_me()
        target = me.id
    photos = await client.get_profile_photos(target, limit=1)
    if photos:
        await e.reply(file=photos[0])
        await e.delete()
    else:
        await e.edit("❌ Аватарка не найдена")

@client.on(events.NewMessage(pattern=r'/name (.+)', from_users='me'))
async def name_cmd(e):
    new_name = e.pattern_match.group(1).strip()
    await client.edit_profile(first_name=new_name)
    await e.edit(f"✅ Имя изменено на: **{new_name}**")

@client.on(events.NewMessage(pattern=r'/lastname(?:\s+(.+))?', from_users='me'))
async def lastname_cmd(e):
    new_lastname = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(last_name=new_lastname)
    if new_lastname:
        await e.edit(f"✅ Фамилия изменена на: **{new_lastname}**")
    else:
        await e.edit("✅ Фамилия удалена")

@client.on(events.NewMessage(pattern=r'/bio(?:\s+(.+))?', from_users='me'))
async def bio_cmd(e):
    new_bio = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(about=new_bio)
    await e.edit(f"✅ Био обновлено{f': _{new_bio}_' if new_bio else ' (очищено)'}")

@client.on(events.NewMessage(pattern=r'/username_check (.+)', from_users='me'))
async def username_check_cmd(e):
    username = e.pattern_match.group(1).strip().lstrip('@')
    try:
        entity = await client.get_entity(username)
        name = getattr(entity, 'first_name', None) or getattr(entity, 'title', 'Неизвестно')
        await e.edit(f"🔍 @{username}\n✅ **Занят**\n👤 Имя: {name}\n🆔 ID: `{entity.id}`")
    except:
        await e.edit(f"🔍 @{username}\n❌ **Свободен** или не найден")

# ══════════════════════════════════════════════════
#  ИГРЫ И РАЗВЛЕЧЕНИЯ
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/dice', from_users='me'))
async def dice_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice(emoticon='🎲'))

@client.on(events.NewMessage(pattern=r'/dart', from_users='me'))
async def dart_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice(emoticon='🎯'))

@client.on(events.NewMessage(pattern=r'/basket', from_users='me'))
async def basket_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice(emoticon='🏀'))

@client.on(events.NewMessage(pattern=r'/football', from_users='me'))
async def football_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice(emoticon='⚽'))

@client.on(events.NewMessage(pattern=r'/coin', from_users='me'))
async def coin_cmd(e):
    result = random.choice(["Орёл 🦅", "Решка 💰"])
    flips = random.randint(3, 7)
    await e.edit(f"🪙 Монета подброшена ({flips} оборотов)!\n\nРезультат: **{result}**")

@client.on(events.NewMessage(pattern=r'/rand(?:\s+(-?\d+)(?:\s+(-?\d+))?)?', from_users='me'))
async def rand_cmd(e):
    g = e.pattern_match
    if g.group(1) and g.group(2):
        min_val, max_val = int(g.group(1)), int(g.group(2))
        if min_val > max_val:
            min_val, max_val = max_val, min_val
        num = random.randint(min_val, max_val)
        await e.edit(f"🎲 Случайное число от `{min_val}` до `{max_val}`: **{num}**")
    elif g.group(1):
        await e.edit(f"🎲 Случайное число от `1` до `{g.group(1)}`: **{random.randint(1, int(g.group(1)))}**")
    else:
        await e.edit(f"🎲 Случайное число: **{random.randint(1, 100)}**")

@client.on(events.NewMessage(pattern=r'/8ball(?:\s+(.+))?', from_users='me'))
async def eightball_cmd(e):
    # Категории ответов с весами (позитив/нейтраль/негатив)
    positive = [
        ("Определённо да", "✅", "Вселенная согласна с тобой."),
        ("Без сомнений — да", "💯", "Даже звёзды кивают."),
        ("Скорее всего да", "👍", "Всё складывается в твою пользу."),
        ("Хорошие перспективы", "🌟", "Будущее выглядит светлым."),
        ("Знаки говорят «да»", "🔮", "Мистические силы на твоей стороне."),
        ("Всё указывает на «да»", "💫", "Судьба уже всё решила."),
        ("Да, и поскорее", "🚀", "Не медли — действуй прямо сейчас."),
        ("Абсолютно точно да", "🏆", "Лучшего ответа не существует."),
        ("Это неизбежно", "⚡", "Ничто не остановит это."),
        ("Да, если сделаешь первый шаг", "🦶", "Действие — ключ к результату."),
    ]
    neutral = [
        ("Пока не ясно", "🤔", "Туман будущего слишком густой."),
        ("Спроси позже", "⏰", "Момент ещё не настал."),
        ("Не могу предсказать", "🌫", "Слишком много переменных."),
        ("Сосредоточься и спроси снова", "🧘", "Твой разум мешает ответу."),
        ("Лучше не рассказывать", "🤫", "Некоторые тайны лучше хранить."),
        ("Трудно сказать", "😶", "Даже я не всесилен."),
        ("Возможно, но не сейчас", "🌙", "Подожди подходящего момента."),
    ]
    negative = [
        ("Мой ответ — нет", "🚫", "Прими это спокойно."),
        ("Перспективы не очень", "😕", "Стоит пересмотреть планы."),
        ("Весьма сомнительно", "🙄", "Интуиция говорит «осторожно»."),
        ("Точно нет", "💀", "Даже не думай об этом."),
        ("Не рассчитывай на это", "❌", "Лучше найди другой путь."),
        ("Нет — и это окончательно", "🔴", "Вселенная категорически против."),
        ("Всё против этого", "⛈", "Сейчас не лучшее время."),
        ("Откажись от этой идеи", "🗑", "Это дорога в никуда."),
    ]

    question = (e.pattern_match.group(1) or "").strip()

    # Анимация шара
    frames = ["🎱 ...", "🎱 ·· ·", "🎱 · · ·", "🎱 ···"]
    msg = await e.edit(random.choice(frames))
    for frame in frames:
        await msg.edit(frame)
        await asyncio.sleep(0.4)

    # Выбор ответа с учётом веса (40% позитив, 30% нейтраль, 30% негатив)
    pool_choice = random.choices(['pos', 'neu', 'neg'], weights=[40, 30, 30])[0]
    if pool_choice == 'pos':
        answer, emoji, comment = random.choice(positive)
        color = "🟢"
    elif pool_choice == 'neu':
        answer, emoji, comment = random.choice(neutral)
        color = "🟡"
    else:
        answer, emoji, comment = random.choice(negative)
        color = "🔴"

    q_line = f"❓ _{question}_\n\n" if question else ""
    result = (
        f"🎱 **Магический шар**\n\n"
        f"{q_line}"
        f"{'─' * 20}\n"
        f"{emoji} **{answer}**\n"
        f"{'─' * 20}\n\n"
        f"💬 _{comment}_\n\n"
        f"{color} Уверенность: **{random.randint(60, 99)}%**"
    )
    await msg.edit(result)

@client.on(events.NewMessage(pattern=r'/rps(?:\s+(камень|ножницы|бумага|к|н|б))?', from_users='me'))
async def rps_cmd(e):
    choices = {'к': 'Камень 🪨', 'камень': 'Камень 🪨', 'н': 'Ножницы ✂️', 'ножницы': 'Ножницы ✂️', 'б': 'Бумага 📄', 'бумага': 'Бумага 📄'}
    bot_choices = ['Камень 🪨', 'Ножницы ✂️', 'Бумага 📄']
    arg = (e.pattern_match.group(1) or '').lower()
    if not arg:
        await e.edit("✊✌️🖐 Использование: `/rps камень/ножницы/бумага` (или к/н/б)")
        return
    user_choice = choices.get(arg)
    bot_choice = random.choice(bot_choices)
    wins = {'Камень 🪨': 'Ножницы ✂️', 'Ножницы ✂️': 'Бумага 📄', 'Бумага 📄': 'Камень 🪨'}
    if user_choice == bot_choice:
        result = "🤝 **Ничья!**"
    elif wins[user_choice] == bot_choice:
        result = "🏆 **Ты победил!**"
    else:
        result = "💀 **Бот победил!**"
    await e.edit(f"✊✌️🖐 **Камень, Ножницы, Бумага!**\n\n👤 Ты: {user_choice}\n🤖 Бот: {bot_choice}\n\n{result}")

@client.on(events.NewMessage(pattern=r'/slot', from_users='me'))
async def slot_cmd(e):
    symbols = ['🍒', '🍋', '🍊', '🍇', '🍉', '⭐', '💎', '7️⃣']
    s = [random.choice(symbols) for _ in range(3)]
    if s[0] == s[1] == s[2]:
        if s[0] == '💎':
            result = "💰💰💰 ДЖЕКПОТ! БРИЛЛИАНТОВЫЙ ВЫИГРЫШ!"
        elif s[0] == '7️⃣':
            result = "🎰🎰🎰 ДЖЕКПОТ! Тройная семёрка!"
        else:
            result = "🎊 Выигрыш! Три одинаковых!"
    elif s[0] == s[1] or s[1] == s[2] or s[0] == s[2]:
        result = "😅 Почти! Два одинаковых — попробуй ещё"
    else:
        result = "💸 Нет удачи. Попробуй снова!"
    await e.edit(f"🎰 **Слот-машина**\n\n[ {s[0]} | {s[1]} | {s[2]} ]\n\n{result}")

@client.on(events.NewMessage(pattern=r'/lucky', from_users='me'))
async def lucky_cmd(e):
    percent = random.randint(0, 100)
    bar_filled = percent // 10
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    if percent == 100:
        msg = "🌟 АБСОЛЮТНАЯ УДАЧА! Сегодня твой день!"
    elif percent >= 80:
        msg = "🍀 Очень удачный день!"
    elif percent >= 60:
        msg = "😊 Неплохой день, удача на твоей стороне"
    elif percent >= 40:
        msg = "😐 Средняя удача, будь осторожен"
    elif percent >= 20:
        msg = "😬 Не лучший день..."
    else:
        msg = "💀 Сидеть дома и не высовываться!"
    await e.edit(f"🔮 **Индекс удачи на сегодня**\n\n[{bar}] **{percent}%**\n\n{msg}")

# ══════════════════════════════════════════════════
#  УТИЛИТЫ
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/calc (.+)', from_users='me'))
async def calc_cmd(e):
    expr = e.pattern_match.group(1).strip()
    result = await safe_eval(expr)
    if result is not None:
        await e.edit(f"🧮 `{expr}` = **{result}**")
    else:
        await e.edit("❌ Ошибка. Разрешены: `+ - * / ( ) % sqrt sin cos tan log abs pow pi e factorial`")

@client.on(events.NewMessage(pattern=r'/remind (\d+)\s+(.*)', from_users='me'))
async def remind_cmd(e):
    delay = int(e.pattern_match.group(1))
    text = e.pattern_match.group(2).strip()
    readable = fmt_seconds(delay)
    await e.edit(f"⏰ Напоминание через **{readable}**\n📝 _{text}_")
    asyncio.create_task(send_reminder(e.chat_id, text, delay))

@client.on(events.NewMessage(pattern=r'/search (.+)', from_users='me'))
async def search_cmd(e):
    query = e.pattern_match.group(1).strip()
    q_enc = query.replace(' ', '+')
    google_url = f"https://www.google.com/search?q={q_enc}"
    ddg_url = f"https://duckduckgo.com/?q={q_enc}"
    yt_url = f"https://www.youtube.com/results?search_query={q_enc}"
    await e.edit(
        f"🔍 **Поиск:** _{query}_\n\n"
        f"• [Google]({google_url})\n"
        f"• [DuckDuckGo]({ddg_url})\n"
        f"• [YouTube]({yt_url})"
    )

@client.on(events.NewMessage(pattern=r'/shorten (.+)', from_users='me'))
async def shorten_cmd(e):
    import aiohttp
    url = e.pattern_match.group(1).strip()
    await e.edit("⏳ Сокращаю ссылку...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://tinyurl.com/api-create.php?url={url}", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                short = await resp.text()
                if short.startswith('http'):
                    await e.edit(f"🔗 **Оригинал:** `{url[:60]}{'...' if len(url)>60 else ''}`\n✂️ **Короткая:** {short.strip()}")
                else:
                    raise Exception("bad response")
    except:
        await e.edit("❌ Ошибка при сокращении. Проверьте URL.")

@client.on(events.NewMessage(pattern=r'/weather (.+)', from_users='me'))
async def weather_cmd(e):
    city = e.pattern_match.group(1).strip()
    city_enc = city.replace(' ', '+')
    await e.edit(
        f"🌤️ **Погода:** _{city}_\n\n"
        f"🔗 [Открыть прогноз](https://wttr.in/{city_enc})\n"
        f"📍 [OpenWeatherMap](https://openweathermap.org/find?q={city_enc})\n"
        f"🌐 [Weather.com](https://weather.com/ru-RU/weather/today/l/{city_enc})"
    )

@client.on(events.NewMessage(pattern=r'/translate (.+)', from_users='me'))
async def translate_cmd(e):
    text = e.pattern_match.group(1).strip()
    text_enc = text.replace(' ', '%20')
    await e.edit(
        f"🌐 **Перевод текста:**\n_{text}_\n\n"
        f"• [RU→EN](https://translate.google.com/?sl=ru&tl=en&text={text_enc})\n"
        f"• [EN→RU](https://translate.google.com/?sl=en&tl=ru&text={text_enc})\n"
        f"• [Auto→RU](https://translate.google.com/?sl=auto&tl=ru&text={text_enc})"
    )

@client.on(events.NewMessage(pattern=r'/base64 (encode|decode) (.+)', from_users='me'))
async def base64_cmd(e):
    mode = e.pattern_match.group(1)
    text = e.pattern_match.group(2).strip()
    try:
        if mode == 'encode':
            result = base64.b64encode(text.encode()).decode()
            await e.edit(f"🔐 **Base64 Encode:**\n`{result}`")
        else:
            result = base64.b64decode(text.encode()).decode()
            await e.edit(f"🔓 **Base64 Decode:**\n`{result}`")
    except:
        await e.edit("❌ Ошибка. Проверьте входные данные.")

@client.on(events.NewMessage(pattern=r'/hash (.+)', from_users='me'))
async def hash_cmd(e):
    text = e.pattern_match.group(1).strip()
    md5 = hashlib.md5(text.encode()).hexdigest()
    sha1 = hashlib.sha1(text.encode()).hexdigest()
    sha256 = hashlib.sha256(text.encode()).hexdigest()
    await e.edit(
        f"#️⃣ **Хэши для:** _{text}_\n\n"
        f"MD5: `{md5}`\n"
        f"SHA1: `{sha1}`\n"
        f"SHA256: `{sha256}`"
    )

@client.on(events.NewMessage(pattern=r'/morse (.+)', from_users='me'))
async def morse_cmd(e):
    text = e.pattern_match.group(1).strip()
    result = morse_encode(text)
    await e.edit(f"📡 **Морзе:**\n_{text}_\n\n`{result}`")

@client.on(events.NewMessage(pattern=r'/caesar (encode|decode) (\d+) (.+)', from_users='me'))
async def caesar_cmd(e):
    mode = e.pattern_match.group(1)
    shift = int(e.pattern_match.group(2))
    text = e.pattern_match.group(3)
    result = caesar_cipher(text, shift, decrypt=(mode == 'decode'))
    emoji = "🔒" if mode == 'encode' else "🔓"
    await e.edit(f"{emoji} **Шифр Цезаря (сдвиг {shift}):**\n_{text}_\n\n`{result}`")

@client.on(events.NewMessage(pattern=r'/password(?:\s+(\d+))?(?:\s+(simple))?', from_users='me'))
async def password_cmd(e):
    length = int(e.pattern_match.group(1) or 16)
    length = max(4, min(length, 128))
    use_symbols = not e.pattern_match.group(2)
    pwd = generate_password(length, use_symbols)
    strength = "🔴 Слабый" if length < 8 else "🟡 Средний" if length < 12 else "🟢 Сильный"
    await e.edit(
        f"🔑 **Сгенерированный пароль** ({length} симв.)\n\n"
        f"`{pwd}`\n\n"
        f"Сила: {strength}\n"
        f"Символы: {'✅' if use_symbols else '❌'}"
    )

@client.on(events.NewMessage(pattern=r'/qr (.+)', from_users='me'))
async def qr_cmd(e):
    text = e.pattern_match.group(1).strip()
    text_enc = text.replace(' ', '+')
    await e.edit(
        f"📱 **QR-код для:**\n_{text}_\n\n"
        f"🔗 [Сгенерировать QR](https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={text_enc})\n"
        f"_(Открой ссылку — там изображение QR-кода)_"
    )

# ══════════════════════════════════════════════════
#  УПРАВЛЕНИЕ СООБЩЕНИЯМИ
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/type (.+)', from_users='me'))
async def type_cmd(e):
    text = e.pattern_match.group(1).strip()
    msg = await e.edit("▌")
    current = ""
    for i, char in enumerate(text):
        current += char
        if i % 3 == 0 or i == len(text) - 1:
            await msg.edit(current + ("▌" if i < len(text) - 1 else ""))
            await asyncio.sleep(0.05)
    await msg.edit(text)

@client.on(events.NewMessage(pattern=r'/echo (.+)', from_users='me'))
async def echo_cmd(e):
    text = e.pattern_match.group(1).strip()
    await e.delete()
    await client.send_message(e.chat_id, text)

@client.on(events.NewMessage(pattern=r'/say (.+)', from_users='me'))
async def say_cmd(e):
    text = e.pattern_match.group(1).strip()
    await e.delete()
    await client.send_message(e.chat_id, text)

# /clean — удаляет свои сообщения (без лимита)
@client.on(events.NewMessage(pattern=r'/clean(?:\s+(\d+))?', from_users='me'))
async def clean_cmd(e):
    limit = int(e.pattern_match.group(1) or 10)
    my_id = (await client.get_me()).id
    await e.delete()
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        if msg.out or (msg.from_id and getattr(msg.from_id, 'user_id', None) == my_id):
            await msg.delete()
            count += 1
            await asyncio.sleep(0.1)
    info = await client.send_message(e.chat_id, f"✅ Удалено **{count}** своих сообщений")
    await asyncio.sleep(3)
    await info.delete()

# /purge — удаляет ЛЮБЫЕ сообщения, без каких-либо ограничений
@client.on(events.NewMessage(pattern=r'/purge(?:\s+(\d+))?', from_users='me'))
async def purge_cmd(e):
    limit = int(e.pattern_match.group(1) or 10)
    await e.delete()
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        await msg.delete()
        count += 1
        await asyncio.sleep(0.05)
    info = await client.send_message(e.chat_id, f"⚠️ Удалено **{count}** сообщений (любых)")
    await asyncio.sleep(3)
    await info.delete()

# /spam — без ограничений на количество
@client.on(events.NewMessage(pattern=r'/spam (\d+) (.+)', from_users='me'))
async def spam_cmd(e):
    count = int(e.pattern_match.group(1))
    text = e.pattern_match.group(2).strip()
    await e.delete()
    for i in range(count):
        await client.send_message(e.chat_id, text)
        await asyncio.sleep(0.4)

@client.on(events.NewMessage(pattern=r'/forward(?:\s+(\d+))?', from_users='me'))
async def forward_cmd(e):
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на сообщение и укажите ID чата: `/forward [chat_id]`")
        return
    args = e.text.split()
    if len(args) < 2:
        await e.edit("ℹ️ Использование: `/forward [chat_id]` (ответом на сообщение)")
        return
    try:
        target = int(args[1])
        msg = await e.get_reply_message()
        await client.forward_messages(target, msg)
        await e.edit(f"✅ Переслано в `{target}`")
    except Exception as ex:
        await e.edit(f"❌ Ошибка: {ex}")

@client.on(events.NewMessage(pattern=r'/pin', from_users='me'))
async def pin_cmd(e):
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на сообщение для закрепления")
        return
    msg = await e.get_reply_message()
    await msg.pin(notify=False)
    await e.delete()

@client.on(events.NewMessage(pattern=r'/unpin', from_users='me'))
async def unpin_cmd(e):
    if e.reply_to_msg_id:
        msg = await e.get_reply_message()
        await msg.unpin()
    else:
        await client.unpin_message(e.chat_id)
    await e.delete()

@client.on(events.NewMessage(pattern=r'/copyall (\d+) (-?\d+)', from_users='me'))
async def copyall_cmd(e):
    """Копирует n последних сообщений в другой чат"""
    count = int(e.pattern_match.group(1))
    target = int(e.pattern_match.group(2))
    await e.edit(f"⏳ Копирую {count} сообщений...")
    msgs = []
    async for msg in client.iter_messages(e.chat_id, limit=count):
        msgs.append(msg)
    msgs.reverse()
    copied = 0
    for msg in msgs:
        try:
            await client.forward_messages(target, msg)
            copied += 1
            await asyncio.sleep(0.5)
        except:
            pass
    await e.edit(f"✅ Скопировано **{copied}/{count}** сообщений в `{target}`")

# ══════════════════════════════════════════════════
#  ЗАМЕТКИ И ХРАНИЛИЩЕ
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/save (\S+) (.+)', from_users='me'))
async def save_cmd(e):
    key, value = e.pattern_match.group(1), e.pattern_match.group(2)
    saved = load_saved()
    saved[key] = value
    write_saved(saved)
    await e.edit(f"✅ Сохранено: `{key}` = _{value}_")

@client.on(events.NewMessage(pattern=r'/get (\S+)', from_users='me'))
async def get_cmd(e):
    key = e.pattern_match.group(1)
    saved = load_saved()
    value = saved.get(key)
    if value:
        await e.edit(f"📦 `{key}` = _{value}_")
    else:
        await e.edit(f"❌ Ключ `{key}` не найден")

@client.on(events.NewMessage(pattern=r'/del (\S+)', from_users='me'))
async def del_cmd(e):
    key = e.pattern_match.group(1)
    saved = load_saved()
    if key in saved:
        del saved[key]
        write_saved(saved)
        await e.edit(f"🗑 Удалено: `{key}`")
    else:
        await e.edit(f"❌ Ключ `{key}` не найден")

@client.on(events.NewMessage(pattern=r'/list', from_users='me'))
async def list_cmd(e):
    saved = load_saved()
    if not saved:
        await e.edit("📭 Нет сохранённых данных")
        return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'...' if len(v)>40 else ''}_" for k, v in saved.items())
    await e.edit(f"📦 **Сохранённые данные** ({len(saved)}):\n\n{items}")

@client.on(events.NewMessage(pattern=r'/note (\S+)(?: (.+))?', from_users='me'))
async def note_cmd(e):
    key = e.pattern_match.group(1)
    text_arg = e.pattern_match.group(2) or ""
    notes = load_notes()
    if e.reply_to_msg_id:
        replied = await e.get_reply_message()
        text_arg = replied.text or text_arg
    if not text_arg:
        await e.edit("ℹ️ Использование: `/note <название> <текст>` или ответом на сообщение")
        return
    notes[key] = text_arg
    write_notes(notes)
    await e.edit(f"📝 Заметка сохранена: `{key}`")

@client.on(events.NewMessage(pattern=r'/getnote (\S+)', from_users='me'))
async def getnote_cmd(e):
    key = e.pattern_match.group(1)
    notes = load_notes()
    if key in notes:
        await e.edit(f"📝 **{key}:**\n\n{notes[key]}")
    else:
        await e.edit(f"❌ Заметка `{key}` не найдена")

@client.on(events.NewMessage(pattern=r'/delnote (\S+)', from_users='me'))
async def delnote_cmd(e):
    key = e.pattern_match.group(1)
    notes = load_notes()
    if key in notes:
        del notes[key]
        write_notes(notes)
        await e.edit(f"🗑 Заметка удалена: `{key}`")
    else:
        await e.edit(f"❌ Заметка `{key}` не найдена")

@client.on(events.NewMessage(pattern=r'/notes', from_users='me'))
async def notes_cmd(e):
    notes = load_notes()
    if not notes:
        await e.edit("📭 Нет заметок")
        return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'...' if len(v)>40 else ''}_" for k, v in notes.items())
    await e.edit(f"📝 **Заметки** ({len(notes)}):\n\n{items}")

# ══════════════════════════════════════════════════
#  AFK
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/afk(?:\s+(.+))?', from_users='me'))
async def afk_cmd(e):
    global afk_start_time, afk_reason
    afk_start_time = time.time()
    afk_reason = (e.pattern_match.group(1) or '').strip()
    reason_line = f"\n📝 Причина: _{afk_reason}_" if afk_reason else ""
    await e.edit(f"😴 **AFK включён**{reason_line}\nВсем кто напишет — уведомлю.")

@client.on(events.NewMessage(pattern=r'/unafk', from_users='me'))
async def unafk_cmd(e):
    global afk_start_time, afk_reason
    if afk_start_time:
        duration = fmt_seconds(time.time() - afk_start_time)
        afk_start_time = None
        afk_reason = ""
        await e.edit(f"☀️ **AFK отключён**\n⏱ Отсутствовал: _{duration}_")
    else:
        await e.edit("ℹ️ AFK не был включён")

# ══════════════════════════════════════════════════
#  ИНФОРМАЦИЯ О ЧАТЕ
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/chatinfo', from_users='me'))
async def chatinfo_cmd(e):
    chat = await e.get_chat()
    chat_id = e.chat_id
    name = getattr(chat, 'title', None) or f"{getattr(chat, 'first_name', '')} {getattr(chat, 'last_name', '')}".strip()
    username = getattr(chat, 'username', None)
    members = getattr(chat, 'participants_count', None)
    chat_type = type(chat).__name__

    lines = [
        f"📊 **Информация о чате**\n",
        f"📛 Название: **{name}**",
        f"🆔 ID: `{chat_id}`",
        f"🔖 Username: @{username}" if username else "🔖 Username: нет",
        f"👥 Тип: `{chat_type}`",
    ]
    if members:
        lines.append(f"👤 Участников: `{members}`")
    await e.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'/members', from_users='me'))
async def members_cmd(e):
    try:
        participants = await client.get_participants(e.chat_id)
        bots = sum(1 for p in participants if p.bot)
        humans = len(participants) - bots
        await e.edit(
            f"👥 **Участники чата**\n\n"
            f"Всего: `{len(participants)}`\n"
            f"👤 Людей: `{humans}`\n"
            f"🤖 Ботов: `{bots}`"
        )
    except Exception as ex:
        await e.edit(f"❌ Не удалось получить список: {ex}")

@client.on(events.NewMessage(pattern=r'/admins', from_users='me'))
async def admins_cmd(e):
    try:
        from telethon.tl.types import ChannelParticipantsAdmins
        admins = await client.get_participants(e.chat_id, filter=ChannelParticipantsAdmins)
        lines = [f"👑 **Администраторы** ({len(admins)}):\n"]
        for a in admins[:20]:
            name = f"{a.first_name or ''} {a.last_name or ''}".strip()
            uname = f"@{a.username}" if a.username else f"`{a.id}`"
            lines.append(f"• {name} — {uname}")
        await e.edit("\n".join(lines))
    except Exception as ex:
        await e.edit(f"❌ Ошибка: {ex}")

@client.on(events.NewMessage(pattern=r'/top(?:\s+(\d+))?', from_users='me'))
async def top_cmd(e):
    """Топ активных участников по сообщениям"""
    limit = int(e.pattern_match.group(1) or 100)
    await e.edit("⏳ Анализирую сообщения...")
    counter = defaultdict(int)
    names = {}
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        if msg.sender_id:
            counter[msg.sender_id] += 1
            sender = await msg.get_sender()
            if sender:
                n = f"{getattr(sender, 'first_name', '') or ''} {getattr(sender, 'last_name', '') or ''}".strip()
                names[msg.sender_id] = n or str(msg.sender_id)
    top = sorted(counter.items(), key=lambda x: x[1], reverse=True)[:10]
    lines = [f"🏆 **Топ активных** (из {limit} сообщений):\n"]
    medals = ["🥇", "🥈", "🥉"] + ["4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    for i, (uid, cnt) in enumerate(top):
        lines.append(f"{medals[i]} {names.get(uid, uid)} — `{cnt}` сообщ.")
    await e.edit("\n".join(lines))

# ══════════════════════════════════════════════════
#  AFK — входящий обработчик
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def incoming_handler(event):
    global auto_reply_enabled, afk_start_time

    sender = await event.get_sender()
    if not sender or sender.bot:
        return

    user_id = event.sender_id
    now = time.time()

    # AFK уведомление
    if afk_start_time and now - reply_cooldown.get(f'afk_{user_id}', 0) > 60:
        duration = fmt_seconds(now - afk_start_time)
        reason_line = f"\n📝 Причина: _{afk_reason}_" if afk_reason else ""
        reply_cooldown[f'afk_{user_id}'] = now
        await event.reply(f"😴 Хозяин сейчас AFK уже **{duration}**{reason_line}")

    # Автоответчик
    if auto_reply_enabled and now - reply_cooldown.get(user_id, 0) > 10:
        reply_cooldown[user_id] = now
        await asyncio.sleep(1)
        await event.reply(auto_reply_text)

# ══════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════

bot_start_time = time.time()

if __name__ == "__main__":
    print("🚀 Запуск UserBot...")
    Thread(target=run_web).start()
    client.start()
    print("✅ UserBot запущен и готов к работе!")
    print("📋 Команд: 50")
    client.run_until_disconnected()
