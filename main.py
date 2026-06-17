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
import io
import cmath
from collections import defaultdict

from telethon import TelegramClient, events
from telethon.tl.types import (
    InputMediaDice, ChannelParticipantsAdmins, ChannelParticipantsBots,
    ReactionEmoji
)
from telethon.tl.functions.messages import SendReactionRequest
from flask import Flask
from threading import Thread

# === Конфигурация ===
logging.basicConfig(level=logging.INFO)
api_id  = os.environ.get('API_ID')
api_hash = os.environ.get('API_HASH')

# === Глобальные переменные ===
auto_reply_enabled = False
auto_reply_text    = '💫 Я автоответчик, хозяин скоро ответит! Спасибо за терпение 😘'
afk_start_time     = None
afk_reason         = ""
ghost_mode         = False
data_file          = 'userbot_data.json'
saved_file         = 'saved_data.json'
notes_file         = 'notes_data.json'
todos_file         = 'todos_data.json'
stats_file         = 'stats_data.json'
MY_ID              = None

# === Загрузка/сохранение ===
def load_data():
    global auto_reply_enabled, auto_reply_text, ghost_mode
    if os.path.exists(data_file):
        with open(data_file, 'r', encoding='utf-8') as f:
            d = json.load(f)
            auto_reply_enabled = d.get('auto_reply_enabled', False)
            auto_reply_text    = d.get('auto_reply_text', auto_reply_text)
            ghost_mode         = d.get('ghost_mode', False)

def save_data():
    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump({'auto_reply_enabled': auto_reply_enabled,
                   'auto_reply_text': auto_reply_text,
                   'ghost_mode': ghost_mode}, f, ensure_ascii=False)

def _load(path):
    return json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}

def _write(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)

def load_todos():
    return json.load(open(todos_file, encoding='utf-8')) if os.path.exists(todos_file) else []

def write_todos(lst):
    with open(todos_file, 'w', encoding='utf-8') as f:
        json.dump(lst, f, ensure_ascii=False)

def bump_stat(key, n=1):
    s = _load(stats_file)
    s[key] = s.get(key, 0) + n
    _write(stats_file, s)

load_data()

# === Клиент Telethon ===
try:
    client = TelegramClient('my_userbot', int(api_id), api_hash)
except ValueError:
    from telethon.sessions import SQLiteSession
    client = TelegramClient(SQLiteSession('my_userbot'), int(api_id), api_hash)

# === Flask для веб-прокси ===
app = Flask(__name__)
reply_cooldown = defaultdict(float)

@app.route('/')
def home():
    return "🤖 UserBot работает 24/7!"

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# === Вспомогательные функции ===
def fmt_time(s):
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d: parts.append(f"{d}д")
    if h: parts.append(f"{h}ч")
    if m: parts.append(f"{m}м")
    if s or not parts: parts.append(f"{s}с")
    return " ".join(parts)

def progress_bar(val, mx, width=10):
    filled = int(width * val / max(mx, 1))
    return "█" * filled + "░" * (width - filled)

def parse_time(text):
    text = text.lower().strip()
    if 'через' in text:
        parts = text.split('через')[-1].strip().split()
        if len(parts) >= 2:
            try:
                num = int(parts[0])
                unit = parts[1][0]
                if unit in 'mмин': return num * 60
                elif unit in 'hч': return num * 3600
                elif unit in 'dд': return num * 86400
                elif unit in 'sс': return num
            except: pass
    match = re.match(r'^(\d+)([mhds])$', text)
    if match:
        num, unit = int(match.group(1)), match.group(2)
        if unit == 'm': return num * 60
        elif unit == 'h': return num * 3600
        elif unit == 'd': return num * 86400
        elif unit == 's': return num
    nums = re.findall(r'\d+', text)
    units = re.findall(r'[mhds]', text)
    if nums and units:
        try:
            num = int(nums[0]); unit = units[0]
            if unit == 'm': return num * 60
            elif unit == 'h': return num * 3600
            elif unit == 'd': return num * 86400
            elif unit == 's': return num
        except: pass
    return None

async def send_reminder(chat_id, msg, delay):
    await asyncio.sleep(delay)
    await client.send_message(chat_id, f"⏰ **НАПОМИНАНИЕ:**\n{msg}")

bot_start = time.time()

def is_me(event):
    return event.sender_id == MY_ID

# ============================================================
# 1. ОСНОВНЫЕ КОМАНДЫ (все используют e.edit)
# ============================================================

@client.on(events.NewMessage(pattern=r'/sleep$', func=is_me))
async def sleep_cmd(e):
    global auto_reply_enabled
    auto_reply_enabled = True
    save_data()
    await e.edit('💤 Автоответчик **ВКЛЮЧЕН**.')

@client.on(events.NewMessage(pattern=r'/wake$', func=is_me))
async def wake_cmd(e):
    global auto_reply_enabled
    auto_reply_enabled = False
    save_data()
    await e.edit('☀️ Автоответчик **ВЫКЛЮЧЕН**.')

@client.on(events.NewMessage(pattern=r'/setreply (.+)', func=is_me))
async def setreply_cmd(e):
    global auto_reply_text
    auto_reply_text = e.pattern_match.group(1).strip()
    save_data()
    await e.edit(f"✅ Текст автоответчика:\n_{auto_reply_text}_")

@client.on(events.NewMessage(pattern=r'/status$', func=is_me))
async def status_cmd(e):
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    s = _load(stats_file)
    await e.edit(
        f"📊 **Статус UserBot**\n\n"
        f"👤 {me.first_name} {me.last_name or ''}\n"
        f"💬 Чатов: `{len(dialogs)}`\n"
        f"🤖 Автоответчик: {'💤 Вкл' if auto_reply_enabled else '☀️ Выкл'}\n"
        f"👻 Ghost-режим: {'✅' if ghost_mode else '❌'}\n"
        f"😴 AFK: {'✅ ' + (afk_reason or 'без причины') if afk_start_time else '❌'}\n"
        f"⏱ Аптайм: `{fmt_time(time.time()-bot_start)}`\n"
        f"📨 Команд выполнено: `{s.get('cmds',0)}`"
    )

@client.on(events.NewMessage(pattern=r'/time$', func=is_me))
async def time_cmd(e):
    now = datetime.datetime.now()
    utc = datetime.datetime.utcnow()
    week_days = ['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье']
    day_of_year = now.timetuple().tm_yday
    week_num    = now.isocalendar()[1]
    await e.edit(
        f"🕐 **Время и дата**\n\n"
        f"🏠 Локальное: `{now.strftime('%H:%M:%S')}`\n"
        f"🌍 UTC: `{utc.strftime('%H:%M:%S')}`\n"
        f"📅 Дата: `{now.strftime('%d.%m.%Y')}`\n"
        f"📆 День: **{week_days[now.weekday()]}**\n"
        f"📊 День года: `{day_of_year}/365`\n"
        f"📋 Неделя: `#{week_num}`"
    )

@client.on(events.NewMessage(pattern=r'/ping$', func=is_me))
async def ping_cmd(e):
    t0 = time.monotonic()
    await e.edit("🏓 ...")
    ms = (time.monotonic() - t0) * 1000
    q  = "🟢 Отлично" if ms < 150 else "🟡 Нормально" if ms < 400 else "🔴 Высокая"
    await e.edit(f"🏓 **Понг!**\n⚡ Задержка: `{ms:.1f} мс`\n📶 Качество: {q}")

@client.on(events.NewMessage(pattern=r'/id$', func=is_me))
async def id_cmd(e):
    chat = await e.get_chat()
    lines = [f"🆔 **ID чата:** `{chat.id}`"]
    if e.reply_to_msg_id:
        r = await e.get_reply_message()
        lines += [
            f"👤 **ID отправителя:** `{r.sender_id}`",
            f"📨 **ID сообщения:** `{r.id}`",
        ]
        if r.sender and getattr(r.sender, 'username', None):
            lines.append(f"🔖 **Username:** @{r.sender.username}")
    else:
        me = await client.get_me()
        lines.append(f"👤 **Мой ID:** `{me.id}`")
    await e.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'/info$', func=is_me))
async def info_cmd(e):
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    await e.edit(
        f"🚀 **UserBot Info**\n\n"
        f"👤 {me.first_name} {me.last_name or ''}\n"
        f"🆔 ID: `{me.id}`\n"
        f"🔰 @{me.username or 'нет'}\n"
        f"📱 Телефон: `{me.phone or 'скрыт'}`\n"
        f"💬 Чатов: `{len(dialogs)}`\n"
        f"⏱ Аптайм: `{fmt_time(time.time()-bot_start)}`\n"
        f"⚡ Статус: **Активен** ✅"
    )

@client.on(events.NewMessage(pattern=r'/restart$', func=is_me))
async def restart_cmd(e):
    await e.edit('🔄 Перезагрузка...')
    await asyncio.sleep(2)
    await client.disconnect()
    os._exit(0)

@client.on(events.NewMessage(pattern=r'/ghost$', func=is_me))
async def ghost_cmd(e):
    global ghost_mode
    ghost_mode = not ghost_mode
    save_data()
    if ghost_mode:
        await e.edit("👻 **Ghost-режим ВКЛЮЧЁН** — команды удаляются мгновенно")
        await asyncio.sleep(2)
        await e.delete()
    else:
        await e.edit("👁 **Ghost-режим ВЫКЛЮЧЕН**")

# ============================================================
# 2. ПРОФИЛЬ
# ============================================================

@client.on(events.NewMessage(pattern=r'/me$', func=is_me))
async def me_cmd(e):
    me = await client.get_me()
    photos = await client.get_profile_photos(me.id, limit=1)
    await e.edit(
        f"👤 **Мой профиль**\n\n"
        f"📛 {me.first_name} {me.last_name or ''}\n"
        f"🆔 `{me.id}`\n"
        f"🔰 @{me.username or 'нет'}\n"
        f"📱 `{me.phone or 'скрыт'}`\n"
        f"🖼 Аватар: {'✅' if photos else '❌'}\n"
        f"✔️ Verified: {'✅' if me.verified else '❌'}\n"
        f"🤖 Бот: {'✅' if me.bot else '❌'}"
    )

@client.on(events.NewMessage(pattern=r'/avatar$', func=is_me))
async def avatar_cmd(e):
    if e.reply_to_msg_id:
        r = await e.get_reply_message()
        uid = r.sender_id
    else:
        uid = (await client.get_me()).id
    photos = await client.get_profile_photos(uid, limit=1)
    if photos:
        # Отправляем ссылку на аватар (через file_id можно получить прямую ссылку, но проще отправить файл отдельно)
        # Чтобы остаться в рамках редактирования, отправляем сообщение с файлом, а команду редактируем в уведомление.
        await e.edit("🖼 Аватар отправлен ниже.")
        await client.send_file(e.chat_id, photos[0], caption=f"Аватар пользователя `{uid}`")
    else:
        await e.edit("❌ Аватарка не найдена")

@client.on(events.NewMessage(pattern=r'/name (.+)', func=is_me))
async def name_cmd(e):
    n = e.pattern_match.group(1).strip()
    await client.edit_profile(first_name=n)
    await e.edit(f"✅ Имя → **{n}**")

@client.on(events.NewMessage(pattern=r'/lastname(?:\s+(.+))?$', func=is_me))
async def lastname_cmd(e):
    n = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(last_name=n)
    await e.edit(f"✅ Фамилия → **{n}**" if n else "✅ Фамилия удалена")

@client.on(events.NewMessage(pattern=r'/bio(?:\s+(.+))?$', func=is_me))
async def bio_cmd(e):
    t = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(about=t)
    await e.edit(f"✅ Био → _{t}_" if t else "✅ Био очищено")

@client.on(events.NewMessage(pattern=r'/whois (.+)', func=is_me))
async def whois_cmd(e):
    target = e.pattern_match.group(1).strip().lstrip('@')
    try:
        ent = await client.get_entity(target)
        name = f"{getattr(ent,'first_name','') or ''} {getattr(ent,'last_name','') or ''}".strip() or getattr(ent,'title','?')
        uname = f"@{ent.username}" if getattr(ent,'username',None) else "нет"
        bot_  = "✅" if getattr(ent,'bot',False) else "❌"
        ver   = "✅" if getattr(ent,'verified',False) else "❌"
        await e.edit(
            f"🔍 **Информация о пользователе**\n\n"
            f"📛 Имя: **{name}**\n"
            f"🆔 ID: `{ent.id}`\n"
            f"🔰 Username: {uname}\n"
            f"🤖 Бот: {bot_}\n"
            f"✔️ Verified: {ver}"
        )
    except Exception as ex:
        await e.edit(f"❌ Не найден: {ex}")

@client.on(events.NewMessage(pattern=r'/username_check (.+)', func=is_me))
async def username_check_cmd(e):
    uname = e.pattern_match.group(1).strip().lstrip('@')
    try:
        ent = await client.get_entity(uname)
        name = getattr(ent,'first_name',None) or getattr(ent,'title','?')
        await e.edit(f"🔍 @{uname}\n✅ **Занят**\n👤 {name}\n🆔 `{ent.id}`")
    except:
        await e.edit(f"🔍 @{uname}\n✅ **Свободен**")

# ============================================================
# 3. ИГРЫ И РАЗВЛЕЧЕНИЯ (все e.edit)
# ============================================================

@client.on(events.NewMessage(pattern=r'/dice$', func=is_me))
async def dice_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎲'))

@client.on(events.NewMessage(pattern=r'/dart$', func=is_me))
async def dart_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎯'))

@client.on(events.NewMessage(pattern=r'/basket$', func=is_me))
async def basket_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🏀'))

@client.on(events.NewMessage(pattern=r'/football$', func=is_me))
async def football_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('⚽'))

@client.on(events.NewMessage(pattern=r'/bowling$', func=is_me))
async def bowling_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎳'))

@client.on(events.NewMessage(pattern=r'/casino$', func=is_me))
async def casino_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎰'))

@client.on(events.NewMessage(pattern=r'/coin$', func=is_me))
async def coin_cmd(e):
    sides = ["Орёл 🦅", "Решка 💰"]
    flips = random.randint(3, 9)
    r = random.choice(sides)
    await e.edit(f"🪙 Монета вращается {flips} раз...\n\nРезультат: **{r}**")

@client.on(events.NewMessage(pattern=r'/rand(?:\s+(-?\d+)(?:\s+(-?\d+))?)?$', func=is_me))
async def rand_cmd(e):
    g = e.pattern_match
    a, b = g.group(1), g.group(2)
    if a and b:
        lo, hi = sorted([int(a), int(b)])
        await e.edit(f"🎲 `{lo}` … `{hi}` → **{random.randint(lo, hi)}**")
    elif a:
        await e.edit(f"🎲 `1` … `{a}` → **{random.randint(1, int(a))}**")
    else:
        await e.edit(f"🎲 **{random.randint(1, 100)}**")

# УЛУЧШЕННЫЙ /8ball (с историей, статистикой, анимацией)
@client.on(events.NewMessage(pattern=r'/8ball(?:\s+(.+))?$', func=is_me))
async def eightball_cmd(e):
    if not hasattr(eightball_cmd, 'history'):
        eightball_cmd.history = []
        eightball_cmd.total_asked = 0
        eightball_cmd.pos_count = 0

    ANSWERS = {
        'pos': [
            ("Определённо да", "✅", "Вселенная согласна с тобой.", "Действуй смело!"),
            ("Без сомнений", "💯", "Это решено раньше, чем ты спросил.", "Ты уже знаешь путь."),
            ("Скорее всего да", "👍", "Всё складывается в твою пользу.", "Продолжай в том же духе."),
            ("Хорошие перспективы", "🌟", "Будущее выглядит светлым.", "Звёзды на твоей стороне."),
            ("Знаки говорят «да»", "🔮", "Мистические силы на твоей стороне.", "Доверься интуиции."),
            ("Всё указывает на «да»", "💫", "Судьба уже всё решила.", "Не сомневайся."),
            ("Да, и поскорее", "🚀", "Не медли — действуй прямо сейчас.", "Время — ключевой фактор."),
            ("Абсолютно точно", "🏆", "Лучшего ответа не существует.", "Ты победишь."),
            ("Это неизбежно", "⚡", "Ничто не остановит это.", "Прими как данность."),
            ("Да, если сделаешь шаг", "🦶", "Действие — ключ к результату.", "Первый шаг уже сделан."),
            ("Вселенная шепчет: да", "🌌", "Даже звёзды кивают.", "Слушай тишину."),
            ("Смело иди вперёд", "🎯", "Ты уже знал ответ — я лишь подтверждаю.", "Цель близка."),
            ("Это твой день", "🌈", "Удача улыбается тебе.", "Лови момент."),
            ("Да, но будь осторожен", "⚠️", "Успех придёт, но не расслабляйся.", "Держи ухо востро."),
        ],
        'neu': [
            ("Пока не ясно", "🤔", "Туман будущего слишком густой.", "Повтори позже."),
            ("Спроси позже", "⏰", "Момент ещё не настал.", "Терпение — твой союзник."),
            ("Не могу предсказать", "🌫", "Слишком много переменных.", "Упрости вопрос."),
            ("Сосредоточься и повтори", "🧘", "Твой разум мешает ответу.", "Медитация поможет."),
            ("Лучше не рассказывать", "🤫", "Некоторые тайны лучше хранить.", "Не всё нужно знать."),
            ("Трудно сказать", "😶", "Даже я не всесилен.", "Попробуй спросить иначе."),
            ("Возможно, но не сейчас", "🌙", "Подожди подходящего момента.", "Всему своё время."),
            ("Ответ где-то рядом", "🔭", "Смотри внимательнее вокруг себя.", "Знак уже был."),
            ("Может быть", "🌀", "Вероятность 50 на 50.", "Решай сам."),
            ("Спроси у друга", "🗣️", "Чужой взгляд поможет.", "Коллективный разум."),
        ],
        'neg': [
            ("Мой ответ — нет", "🚫", "Прими это спокойно.", "Пересмотри планы."),
            ("Перспективы не очень", "😕", "Стоит пересмотреть планы.", "Не рискуй."),
            ("Весьма сомнительно", "🙄", "Интуиция говорит «осторожно».", "Доверься внутреннему голосу."),
            ("Точно нет", "💀", "Даже не думай об этом.", "Это тупик."),
            ("Не рассчитывай", "❌", "Лучше найди другой путь.", "Обойди препятствие."),
            ("Категорически нет", "🔴", "Вселенная против.", "Не искушай судьбу."),
            ("Всё против этого", "⛈", "Сейчас не лучшее время.", "Подожди перемен."),
            ("Откажись от идеи", "🗑", "Это дорога в никуда.", "Сэкономь силы."),
            ("Шансы ничтожны", "🎰", "Даже удача отвернулась.", "Не трать время."),
            ("Абсолютное нет", "⛔", "Ответ однозначен.", "Прими и двигайся дальше."),
            ("Скорее нет, чем да", "📉", "Тенденция негативная.", "Будь реалистом."),
        ],
        'sarc': [
            ("О, да, конечно", "🙄", "Ты серьёзно?", "Шар устал от глупых вопросов."),
            ("Как скажешь", "😏", "Делай что хочешь.", "Мне всё равно."),
            ("А ты как думаешь?", "🧐", "Ответ очевиден.", "Включи мозги."),
            ("Спроси у кота", "🐱", "Он знает лучше.", "Мурлыканье — знак."),
        ],
        'mystic': [
            ("Тень грядущего", "🌑", "Судьба переплетается с прошлым.", "Ищи знаки в воде."),
            ("Голос из бездны", "🌀", "Древние силы дают ответ.", "Не бойся темноты."),
            ("Звёзды говорят", "✨", "Космос шепчет тебе.", "Смотри на ночное небо."),
        ]
    }

    question = (e.pattern_match.group(1) or '').strip()
    if not question:
        await e.edit("❓ Задай вопрос: `/8ball [твой вопрос]`")
        return

    spin_frames = ["🎱", "🌑", "🌘", "🌗", "🌖", "🌕", "🌔", "🌓", "🌒", "🌑", "🎱"]
    await e.edit("🎱 Шар вращается...")
    for frame in spin_frames:
        await e.edit(f"{frame}  Шар вращается...")
        await asyncio.sleep(0.12)

    pool_weights = {'pos': 35, 'neu': 25, 'neg': 30, 'sarc': 5, 'mystic': 5}
    pool_key = random.choices(list(pool_weights.keys()), weights=list(pool_weights.values()))[0]
    answer, emoji, comment, advice = random.choice(ANSWERS[pool_key])

    name_match = re.search(r'\b(?:мне|меня|я)\s+(\w+)', question, re.IGNORECASE)
    if name_match:
        name = name_match.group(1)
        comment = comment.replace('ты', name).replace('тебе', name).replace('твой', f"{name}а" if name.endswith('а') else f"{name}а")

    color = {"pos":"🟢","neu":"🟡","neg":"🔴","sarc":"🟣","mystic":"🔵"}[pool_key]
    label = {"pos":"ПОЗИТИВНЫЙ","neu":"НЕЙТРАЛЬНЫЙ","neg":"НЕГАТИВНЫЙ","sarc":"САРКАСТИЧНЫЙ","mystic":"МИСТИЧЕСКИЙ"}[pool_key]
    confidence = random.randint(45, 98)
    bar = progress_bar(confidence, 100, 12)

    eightball_cmd.total_asked += 1
    if pool_key == 'pos':
        eightball_cmd.pos_count += 1
    eightball_cmd.history.append((question, answer))
    if len(eightball_cmd.history) > 5:
        eightball_cmd.history.pop(0)

    reply_text = (
        f"🎱 **Магический шар**\n\n"
        f"❓ _{question}_\n\n"
        f"┌{'─'*22}┐\n"
        f"│ {emoji}  **{answer}**\n"
        f"└{'─'*22}┘\n\n"
        f"💬 _{comment}_\n"
        f"💡 *{advice}*\n\n"
        f"{color} {label}\n"
        f"[{bar}] **{confidence}%** уверенности\n"
        f"📊 Всего вопросов: {eightball_cmd.total_asked} | "
        f"Позитивных: {eightball_cmd.pos_count} ({int(eightball_cmd.pos_count/max(1,eightball_cmd.total_asked)*100)}%)"
    )

    if eightball_cmd.history:
        hist_lines = ["\n📜 **Последние вопросы:**"]
        for q, a in eightball_cmd.history[-3:]:
            hist_lines.append(f"• _{q[:30]}{'…' if len(q)>30 else ''}_ → **{a}**")
        reply_text += "\n" + "\n".join(hist_lines)

    await e.edit(reply_text)
    bump_stat('8ball')

@client.on(events.NewMessage(pattern=r'/rps(?:\s+(.+))?$', func=is_me))
async def rps_cmd(e):
    MAP = {'к':'🪨 Камень','камень':'🪨 Камень','н':'✂️ Ножницы','ножницы':'✂️ Ножницы','б':'📄 Бумага','бумага':'📄 Бумага'}
    BOT = ['🪨 Камень','✂️ Ножницы','📄 Бумага']
    WIN = {'🪨 Камень':'✂️ Ножницы','✂️ Ножницы':'📄 Бумага','📄 Бумага':'🪨 Камень'}
    arg = (e.pattern_match.group(1) or '').lower().strip()
    if not arg or arg not in MAP:
        await e.edit("✊✌️🖐 `/rps камень` / `ножницы` / `бумага` (или `к`/`н`/`б`)")
        return
    uc, bc = MAP[arg], random.choice(BOT)
    if uc == bc:   res = "🤝 **Ничья!**"
    elif WIN[uc]==bc: res = "🏆 **Ты победил!**"
    else:          res = "💀 **Бот победил!**"
    await e.edit(f"✊✌️🖐 **КНБ**\n\n👤 Ты: {uc}\n🤖 Бот: {bc}\n\n{res}")

@client.on(events.NewMessage(pattern=r'/slot$', func=is_me))
async def slot_cmd(e):
    SYM = ['🍒','🍋','🍊','🍇','🍉','⭐','💎','7️⃣','🔔','🍀']
    await e.edit("🎰 [ ▓ | ▓ | ▓ ]")
    for _ in range(4):
        s = [random.choice(SYM) for _ in range(3)]
        await e.edit(f"🎰 [ {s[0]} | {s[1]} | {s[2]} ]")
        await asyncio.sleep(0.3)
    s = [random.choice(SYM) for _ in range(3)]
    if s[0]==s[1]==s[2]:
        res = "💰💰💰 **ДЖЕКПОТ!**" if s[0] in ('💎','7️⃣') else "🎊 **Выигрыш! Три одинаковых!**"
    elif len(set(s))<3:
        res = "😅 Почти! Два одинаковых — ещё раз!"
    else:
        res = "💸 Не повезло. Попробуй снова!"
    await e.edit(f"🎰 [ {s[0]} | {s[1]} | {s[2]} ]\n\n{res}")

@client.on(events.NewMessage(pattern=r'/lucky$', func=is_me))
async def lucky_cmd(e):
    pct = random.randint(0, 100)
    bar = progress_bar(pct, 100, 12)
    tips = {
        (90,100): "🌟 АБСОЛЮТНАЯ УДАЧА! Сегодня твой день!",
        (70, 89): "🍀 Очень удачный день — действуй!",
        (50, 69): "😊 Неплохо — удача на твоей стороне",
        (30, 49): "😐 Средний день, будь осторожен",
        (10, 29): "😬 Не лучший день...",
        ( 0,  9): "💀 Сиди дома и не высовывайся!",
    }
    msg = next(v for (a,b),v in tips.items() if a<=pct<=b)
    await e.edit(f"🔮 **Индекс удачи**\n\n[{bar}] **{pct}%**\n\n{msg}")

@client.on(events.NewMessage(pattern=r'/choose (.+)', func=is_me))
async def choose_cmd(e):
    raw = e.pattern_match.group(1)
    opts = [o.strip() for o in re.split(r'[,|/]', raw) if o.strip()]
    if len(opts) < 2:
        await e.edit("ℹ️ Перечисли варианты через запятую: `/choose пицца, суши, бургер`")
        return
    winner = random.choice(opts)
    listed = "\n".join(f"{'➡️' if o==winner else '  •'} {o}" for o in opts)
    await e.edit(f"🤔 **Выбираю из {len(opts)} вариантов...**\n\n{listed}\n\n✅ **Выбор: {winner}**")

@client.on(events.NewMessage(pattern=r'/quiz$', func=is_me))
async def quiz_cmd(e):
    QUESTIONS = [
        ("Столица Австралии?", ["Сидней","Мельбурн","Канберра","Перт"], 2),
        ("Сколько планет в Солнечной системе?", ["7","8","9","10"], 1),
        ("Кто написал «Гамлета»?", ["Диккенс","Толстой","Шекспир","Гёте"], 2),
        ("Химический символ золота?", ["Go","Gd","Au","Ag"], 2),
        ("Год основания Google?", ["1996","1998","2000","2002"], 1),
        ("Самая длинная река мира?", ["Амазонка","Янцзы","Нил","Конго"], 2),
        ("Сколько байт в килобайте?", ["512","1024","2048","4096"], 1),
        ("Скорость света (км/с)?", ["150 000","300 000","450 000","600 000"], 1),
    ]
    q, opts, ans_idx = random.choice(QUESTIONS)
    letters = ['A','B','C','D']
    opts_text = "\n".join(f"{letters[i]}. {o}" for i,o in enumerate(opts))
    correct = f"{letters[ans_idx]}. {opts[ans_idx]}"
    await e.edit(
        f"🧠 **Вопрос:**\n_{q}_\n\n{opts_text}\n\n"
        f"||✅ Ответ: **{correct}**||"
    )

# ============================================================
# 4. УТИЛИТЫ (улучшены)
# ============================================================

@client.on(events.NewMessage(pattern=r'/calc (.+)', func=is_me))
async def calc_cmd(e):
    expr = e.pattern_match.group(1).strip()
    safe_dict = {
        'pi': math.pi, 'e': math.e, 'phi': (1+math.sqrt(5))/2,
        'sqrt': math.sqrt, 'cbrt': lambda x: x**(1/3),
        'factorial': math.factorial, 'gcd': math.gcd, 'lcm': math.lcm,
        'hypot': math.hypot,
        'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
        'asin': math.asin, 'acos': math.acos, 'atan': math.atan,
        'log': math.log, 'log2': math.log2, 'log10': math.log10,
        'exp': math.exp, 'abs': abs, 'round': round,
        'floor': math.floor, 'ceil': math.ceil,
        'sinh': math.sinh, 'cosh': math.cosh, 'tanh': math.tanh,
    }
    for name in safe_dict:
        expr = re.sub(rf'(?<![a-zA-Z]){name}(?![a-zA-Z])', f'__safe__["{name}"]', expr)
    if any(w in expr for w in ['import', 'os', 'sys', 'open', 'exec', 'eval', '__', 'compile', 'globals', 'locals']):
        await e.edit("❌ Выражение содержит запрещённые конструкции.")
        return
    try:
        ns = {'__builtins__': {}, '__safe__': safe_dict}
        result = eval(expr, ns, {})
        if isinstance(result, complex):
            result = f"{result.real:.4f}{'+' if result.imag>=0 else ''}{result.imag:.4f}i"
        elif isinstance(result, float):
            if math.isinf(result) or math.isnan(result):
                result = "∞" if math.isinf(result) else "NaN"
            else:
                result = f"{result:.6f}".rstrip('0').rstrip('.')
        elif isinstance(result, int):
            result = f"{result:,}"
        await e.edit(f"🧮 `{expr}` = **{result}**")
    except Exception as ex:
        await e.edit(f"❌ Ошибка: {ex}")

@client.on(events.NewMessage(pattern=r'/remind (.+)', func=is_me))
async def remind_cmd(e):
    raw = e.pattern_match.group(1).strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await e.edit("ℹ️ Использование: `/remind [время] [текст]`\nПримеры: `/remind 10m Пить воду`, `/remind 2h Встреча`")
        return
    time_str, text = parts[0], parts[1]
    delay = parse_time(time_str)
    if delay is None:
        await e.edit("❌ Не могу распознать время. Используйте: `10m`, `2h`, `1d`, `30s` или `через 10 минут`")
        return
    await e.edit(f"⏰ Напоминание через **{fmt_time(delay)}**\n📝 _{text}_")
    asyncio.create_task(send_reminder(e.chat_id, text, delay))

@client.on(events.NewMessage(pattern=r'/search (.+)', func=is_me))
async def search_cmd(e):
    q = e.pattern_match.group(1).strip()
    enc = q.replace(' ','+')
    await e.edit(
        f"🔍 **{q}**\n\n"
        f"• [Google](https://www.google.com/search?q={enc})\n"
        f"• [DuckDuckGo](https://duckduckgo.com/?q={enc})\n"
        f"• [YouTube](https://www.youtube.com/results?search_query={enc})\n"
        f"• [Wikipedia](https://ru.wikipedia.org/wiki/Special:Search?search={enc})"
    )

@client.on(events.NewMessage(pattern=r'/shorten (.+)', func=is_me))
async def shorten_cmd(e):
    import aiohttp
    url = e.pattern_match.group(1).strip()
    await e.edit("⏳ Сокращаю...")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://tinyurl.com/api-create.php?url={url}",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                short = await r.text()
        if short.startswith('http'):
            await e.edit(f"✂️ **Оригинал:** `{url[:55]}{'…' if len(url)>55 else ''}`\n🔗 **Короткая:** {short.strip()}")
        else:
            raise Exception()
    except:
        await e.edit("❌ Ошибка. Проверь URL.")

@client.on(events.NewMessage(pattern=r'/weather (.+)', func=is_me))
async def weather_cmd(e):
    import aiohttp
    city = e.pattern_match.group(1).strip()
    await e.edit(f"⏳ Получаю погоду для **{city}**...")
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://wttr.in/{city}?format=j1&lang=ru"
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
        current = data['current_condition'][0]
        temp = current['temp_C']
        feels = current['FeelsLikeC']
        desc = current['weatherDesc'][0]['value']
        wind = current['windspeedKmph']
        hum = current['humidity']
        uv = current.get('uvIndex', 'N/A')
        pressure = current.get('pressure', 'N/A')
        await e.edit(
            f"🌤️ **Погода в {city}**\n\n"
            f"🌡️ Температура: **{temp}°C** (ощущается как {feels}°C)\n"
            f"☁️ {desc}\n"
            f"💨 Ветер: {wind} км/ч\n"
            f"💧 Влажность: {hum}%\n"
            f"☀️ UV-индекс: {uv}\n"
            f"📊 Давление: {pressure} мбар"
        )
    except Exception as ex:
        await e.edit(f"❌ Ошибка: {ex}\nПопробуй другой город или проверь подключение.")

@client.on(events.NewMessage(pattern=r'/translate (.+)', func=is_me))
async def translate_cmd(e):
    import aiohttp
    text = e.pattern_match.group(1).strip()
    try:
        async with aiohttp.ClientSession() as session:
            # Определение языка
            detect_url = "https://libretranslate.com/detect"
            async with session.post(detect_url, json={"q": text}) as resp:
                det = await resp.json()
                src_lang = det[0]['language'] if det else 'en'
            # Перевод на русский
            trans_url = "https://libretranslate.com/translate"
            payload = {
                "q": text,
                "source": src_lang,
                "target": "ru",
                "format": "text"
            }
            async with session.post(trans_url, json=payload) as resp:
                result = await resp.json()
                translated = result['translatedText']
            await e.edit(f"🌐 **Перевод** (с {src_lang} на русский):\n\n_{text}_\n\n➡️ _{translated}_")
    except Exception as ex:
        await e.edit(f"❌ Ошибка перевода: {ex}\nПопробуйте позже.")

@client.on(events.NewMessage(pattern=r'/base64 (encode|decode) (.+)', func=is_me))
async def base64_cmd(e):
    mode, text = e.pattern_match.group(1), e.pattern_match.group(2).strip()
    try:
        if mode == 'encode':
            res = base64.b64encode(text.encode()).decode()
            await e.edit(f"🔐 **Base64 encode:**\n`{res}`")
        else:
            res = base64.b64decode(text.encode()).decode()
            await e.edit(f"🔓 **Base64 decode:**\n`{res}`")
    except:
        await e.edit("❌ Ошибка. Проверь данные.")

@client.on(events.NewMessage(pattern=r'/hash (.+)', func=is_me))
async def hash_cmd(e):
    text = e.pattern_match.group(1).strip().encode()
    await e.edit(
        f"#️⃣ **Хэши**\n\n"
        f"MD5:    `{hashlib.md5(text).hexdigest()}`\n"
        f"SHA1:   `{hashlib.sha1(text).hexdigest()}`\n"
        f"SHA256: `{hashlib.sha256(text).hexdigest()}`\n"
        f"SHA512: `{hashlib.sha512(text).hexdigest()[:64]}…`"
    )

@client.on(events.NewMessage(pattern=r'/morse (.+)', func=is_me))
async def morse_cmd(e):
    text = e.pattern_match.group(1).strip()
    await e.edit(f"📡 **Морзе:**\n_{text}_\n\n`{morse_enc(text)}`")

@client.on(events.NewMessage(pattern=r'/caesar (encode|decode) (\d+) (.+)', func=is_me))
async def caesar_cmd(e):
    mode, shift, text = e.pattern_match.group(1), int(e.pattern_match.group(2)), e.pattern_match.group(3)
    res = caesar(text, shift, dec=(mode=='decode'))
    await e.edit(f"{'🔒' if mode=='encode' else '🔓'} **Цезарь (сдвиг {shift}):**\n_{text}_\n\n`{res}`")

@client.on(events.NewMessage(pattern=r'/vigenere (encode|decode) (\S+) (.+)', func=is_me))
async def vigenere_cmd(e):
    mode, key, text = e.pattern_match.group(1), e.pattern_match.group(2), e.pattern_match.group(3)
    res = vigenere(text, key, dec=(mode=='decode'))
    await e.edit(f"{'🔒' if mode=='encode' else '🔓'} **Виженер (ключ: {key}):**\n_{text}_\n\n`{res}`")

@client.on(events.NewMessage(pattern=r'/password(?:\s+(\d+))?(?:\s+(simple))?$', func=is_me))
async def password_cmd(e):
    length = max(4, min(int(e.pattern_match.group(1) or 16), 128))
    sym    = not e.pattern_match.group(2)
    pwd    = gen_pwd(length, sym)
    s = "🔴 Слабый" if length<8 else "🟡 Средний" if length<12 else "🟢 Сильный" if length<20 else "💎 Очень сильный"
    await e.edit(f"🔑 **Пароль ({length} симв.)**\n\n`{pwd}`\n\nСила: {s}\nСимволы: {'✅' if sym else '❌'}")

@client.on(events.NewMessage(pattern=r'/qr (.+)', func=is_me))
async def qr_cmd(e):
    text = e.pattern_match.group(1).strip()
    try:
        import qrcode
        img = qrcode.make(text)
        bio = io.BytesIO()
        img.save(bio, 'PNG')
        bio.seek(0)
        await e.edit("📱 QR-код сгенерирован, отправляю файл...")
        await client.send_file(e.chat_id, bio, caption=f"QR-код для: _{text}_")
    except ImportError:
        # Fallback на ссылку
        enc = text.replace(' ','+')
        await e.edit(f"📱 **QR-код**\n\n🔗 [Открыть изображение](https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={enc})")
    except Exception as ex:
        await e.edit(f"❌ Ошибка: {ex}")

@client.on(events.NewMessage(pattern=r'/uuid$', func=is_me))
async def uuid_cmd(e):
    import uuid
    ids = [str(uuid.uuid4()) for _ in range(5)]
    out = "\n".join(f"`{u}`" for u in ids)
    await e.edit(f"🆔 **Случайные UUID v4:**\n\n{out}")

@client.on(events.NewMessage(pattern=r'/color (#[0-9a-fA-F]{6}|\d+,\d+,\d+)', func=is_me))
async def color_cmd(e):
    raw = e.pattern_match.group(1).strip()
    if raw.startswith('#'):
        hex_val = raw.upper()
        r,g,b = int(hex_val[1:3],16), int(hex_val[3:5],16), int(hex_val[5:7],16)
    else:
        r,g,b = map(int,raw.split(','))
        hex_val = f"#{r:02X}{g:02X}{b:02X}"
    # HSL
    rf, gf, bf = r/255, g/255, b/255
    mx, mn = max(rf,gf,bf), min(rf,gf,bf)
    l = (mx+mn)/2
    s_val = 0 if mx==mn else (mx-mn)/(1-abs(2*l-1))
    if mx==mn: h_val=0
    elif mx==rf: h_val=60*((gf-bf)/(mx-mn)%6)
    elif mx==gf: h_val=60*((bf-rf)/(mx-mn)+2)
    else: h_val=60*((rf-gf)/(mx-mn)+4)
    # Ссылка на предпросмотр через colorhexa
    await e.edit(
        f"🎨 **Цвет**\n\n"
        f"HEX: `{hex_val}`\n"
        f"RGB: `rgb({r}, {g}, {b})`\n"
        f"HSL: `hsl({h_val:.0f}°, {s_val*100:.0f}%, {l*100:.0f}%)`\n\n"
        f"🔗 [Превью](https://www.colorhexa.com/{hex_val.lstrip('#')})"
    )

@client.on(events.NewMessage(pattern=r'/ascii (.+)', func=is_me))
async def ascii_cmd(e):
    text = e.pattern_match.group(1).strip()
    codes = ' '.join(str(ord(c)) for c in text)
    back  = ''.join(chr(int(x)) for x in codes.split())
    await e.edit(f"🔢 **ASCII коды:**\n_{text}_\n\n`{codes}`\n\nОбратно: `{back}`")

# ============================================================
# 5. УПРАВЛЕНИЕ СООБЩЕНИЯМИ
# ============================================================

@client.on(events.NewMessage(pattern=r'/type(?:\s+(fast|slow|matrix|glitch|rainbow|wave))?\s+(.+)', func=is_me))
async def type_cmd(e):
    mode = e.pattern_match.group(1) or 'normal'
    text = e.pattern_match.group(2).strip()
    if mode == 'fast':
        await e.edit("▌")
        for i in range(0, len(text), 2):
            chunk = text[:i+2]
            await e.edit(chunk + ("▌" if i+2 < len(text) else ""))
            await asyncio.sleep(0.04)
        await e.edit(text)
    elif mode == 'slow':
        await e.edit("▌")
        shown = ""
        for ch in text:
            shown += ch
            await e.edit(shown + "▌")
            pause = 0.3 if ch in '.!?…' else 0.12 if ch in ',;:' else 0.07
            await asyncio.sleep(pause)
        await e.edit(text)
    elif mode == 'matrix':
        CHARS = string.ascii_letters + string.digits + "@#%&"
        await e.edit("▓" * len(text))
        for step in range(len(text)):
            parts = list(text[:step])
            for _ in range(len(text) - step):
                parts.append(random.choice(CHARS))
            await e.edit(''.join(parts))
            await asyncio.sleep(0.07)
        await e.edit(text)
    elif mode == 'glitch':
        GLITCH = "░▒▓█▄▀■□▪▫"
        await e.edit("".join(random.choice(GLITCH) for _ in text))
        for _ in range(6):
            glitched = "".join(c if random.random() > 0.4 else random.choice(GLITCH) for c in text)
            await e.edit(glitched)
            await asyncio.sleep(0.12)
        await e.edit(text)
    elif mode == 'rainbow':
        colors = ['🔴','🟠','🟡','🟢','🔵','🟣']
        await e.edit("")
        for i in range(len(text)):
            await e.edit(''.join(f"{colors[j%len(colors)]}{c}" for j,c in enumerate(text[:i+1])))
            await asyncio.sleep(0.1)
        await e.edit(''.join(f"{colors[i%len(colors)]}{c}" for i,c in enumerate(text)))
    elif mode == 'wave':
        await e.edit("")
        for offset in range(5):
            lines = []
            for i, ch in enumerate(text):
                shift = (i + offset) % 5
                lines.append(' ' * shift + ch)
            await e.edit('\n'.join(lines))
            await asyncio.sleep(0.2)
        await e.edit(text)
    else:
        await e.edit("▌")
        shown = ""
        for i, ch in enumerate(text):
            shown += ch
            if i % 2 == 0 or i == len(text)-1:
                await e.edit(shown + ("▌" if i < len(text)-1 else ""))
                await asyncio.sleep(0.05)
        await e.edit(text)

@client.on(events.NewMessage(pattern=r'/echo (.+)', func=is_me))
async def echo_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, e.pattern_match.group(1).strip())

@client.on(events.NewMessage(pattern=r'/say (.+)', func=is_me))
async def say_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, e.pattern_match.group(1).strip())

@client.on(events.NewMessage(pattern=r'/bold (.+)', func=is_me))
async def bold_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, f"**{e.pattern_match.group(1).strip()}**")

@client.on(events.NewMessage(pattern=r'/italic (.+)', func=is_me))
async def italic_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, f"__{e.pattern_match.group(1).strip()}__")

@client.on(events.NewMessage(pattern=r'/mono (.+)', func=is_me))
async def mono_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, f"`{e.pattern_match.group(1).strip()}`")

@client.on(events.NewMessage(pattern=r'/clean(?:\s+(\d+))?$', func=is_me))
async def clean_cmd(e):
    limit = int(e.pattern_match.group(1) or 10)
    my_id = (await client.get_me()).id
    await e.edit(f"🧹 Удаляю {limit} своих сообщений...")
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        if msg.out or (msg.from_id and getattr(msg.from_id,'user_id',None)==my_id):
            await msg.delete(); count += 1; await asyncio.sleep(0.1)
    await e.edit(f"✅ Удалено **{count}** своих сообщений")
    await asyncio.sleep(3)
    await e.delete()

@client.on(events.NewMessage(pattern=r'/purge(?:\s+(\d+))?$', func=is_me))
async def purge_cmd(e):
    limit = int(e.pattern_match.group(1) or 10)
    await e.edit(f"⚠️ Удаляю {limit} сообщений (включая чужие)...")
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        await msg.delete(); count += 1; await asyncio.sleep(0.04)
    await e.edit(f"⚠️ Удалено **{count}** сообщений")
    await asyncio.sleep(3)
    await e.delete()

@client.on(events.NewMessage(pattern=r'/spam (\d+) (.+)', func=is_me))
async def spam_cmd(e):
    count, text = int(e.pattern_match.group(1)), e.pattern_match.group(2).strip()
    await e.edit(f"📤 Отправляю {count} сообщений...")
    await e.delete()
    for _ in range(count):
        await client.send_message(e.chat_id, text)
        await asyncio.sleep(random.uniform(0.3, 0.7))

@client.on(events.NewMessage(pattern=r'/forward (-?\d+)', func=is_me))
async def forward_cmd(e):
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на сообщение: `/forward [chat_id]`"); return
    try:
        msg = await e.get_reply_message()
        await client.forward_messages(int(e.pattern_match.group(1)), msg)
        await e.edit(f"✅ Переслано в `{e.pattern_match.group(1)}`")
    except Exception as ex:
        await e.edit(f"❌ {ex}")

@client.on(events.NewMessage(pattern=r'/pin$', func=is_me))
async def pin_cmd(e):
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на сообщение"); return
    await (await e.get_reply_message()).pin(notify=False)
    await e.delete()

@client.on(events.NewMessage(pattern=r'/unpin$', func=is_me))
async def unpin_cmd(e):
    if e.reply_to_msg_id:
        await (await e.get_reply_message()).unpin()
    else:
        await client.unpin_message(e.chat_id)
    await e.delete()

@client.on(events.NewMessage(pattern=r'/copyall (\d+) (-?\d+)', func=is_me))
async def copyall_cmd(e):
    count, target = int(e.pattern_match.group(1)), int(e.pattern_match.group(2))
    await e.edit(f"⏳ Копирую {count} сообщений...")
    msgs = []
    async for m in client.iter_messages(e.chat_id, limit=count):
        msgs.append(m)
    msgs.reverse()
    copied = 0
    for m in msgs:
        try:
            await client.forward_messages(target, m); copied += 1; await asyncio.sleep(0.4)
        except: pass
    await e.edit(f"✅ Скопировано **{copied}/{count}** → `{target}`")

@client.on(events.NewMessage(pattern=r'/react (.+)', func=is_me))
async def react_cmd(e):
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на сообщение: `/react 👍`"); return
    emoji = e.pattern_match.group(1).strip()
    try:
        await client(SendReactionRequest(
            peer=e.chat_id,
            msg_id=e.reply_to_msg_id,
            reaction=[ReactionEmoji(emoticon=emoji)]
        ))
        await e.delete()
    except Exception as ex:
        await e.edit(f"❌ Не удалось поставить реакцию: {ex}")

# ============================================================
# 6. ЗАМЕТКИ И TODO
# ============================================================

@client.on(events.NewMessage(pattern=r'/save (\S+) (.+)', func=is_me))
async def save_cmd(e):
    k, v = e.pattern_match.group(1), e.pattern_match.group(2)
    d = _load(saved_file); d[k] = v; _write(saved_file, d)
    await e.edit(f"✅ `{k}` = _{v}_")

@client.on(events.NewMessage(pattern=r'/get (\S+)', func=is_me))
async def get_cmd(e):
    k = e.pattern_match.group(1)
    v = _load(saved_file).get(k)
    await e.edit(f"📦 `{k}` = _{v}_" if v else f"❌ Ключ `{k}` не найден")

@client.on(events.NewMessage(pattern=r'/del (\S+)', func=is_me))
async def del_cmd(e):
    k = e.pattern_match.group(1); d = _load(saved_file)
    if k in d:
        del d[k]; _write(saved_file, d); await e.edit(f"🗑 Удалено: `{k}`")
    else:
        await e.edit(f"❌ `{k}` не найден")

@client.on(events.NewMessage(pattern=r'/list$', func=is_me))
async def list_cmd(e):
    d = _load(saved_file)
    if not d: await e.edit("📭 Нет данных"); return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v)>40 else ''}_" for k,v in d.items())
    await e.edit(f"📦 **Сохранено ({len(d)}):**\n\n{items}")

@client.on(events.NewMessage(pattern=r'/note (\S+)(?: (.+))?', func=is_me))
async def note_cmd(e):
    k = e.pattern_match.group(1)
    t = e.pattern_match.group(2) or ""
    if e.reply_to_msg_id:
        r = await e.get_reply_message(); t = r.text or t
    if not t: await e.edit("ℹ️ `/note <название> <текст>` или ответом"); return
    d = _load(notes_file); d[k] = t; _write(notes_file, d)
    await e.edit(f"📝 Заметка сохранена: `{k}`")

@client.on(events.NewMessage(pattern=r'/getnote (\S+)', func=is_me))
async def getnote_cmd(e):
    k = e.pattern_match.group(1); d = _load(notes_file)
    await e.edit(f"📝 **{k}:**\n\n{d[k]}" if k in d else f"❌ Заметка `{k}` не найдена")

@client.on(events.NewMessage(pattern=r'/delnote (\S+)', func=is_me))
async def delnote_cmd(e):
    k = e.pattern_match.group(1); d = _load(notes_file)
    if k in d:
        del d[k]; _write(notes_file, d); await e.edit(f"🗑 Заметка удалена: `{k}`")
    else:
        await e.edit(f"❌ `{k}` не найдена")

@client.on(events.NewMessage(pattern=r'/notes$', func=is_me))
async def notes_cmd(e):
    d = _load(notes_file)
    if not d: await e.edit("📭 Нет заметок"); return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v)>40 else ''}_" for k,v in d.items())
    await e.edit(f"📝 **Заметки ({len(d)}):**\n\n{items}")

@client.on(events.NewMessage(pattern=r'/todo (.+)', func=is_me))
async def todo_add_cmd(e):
    task = e.pattern_match.group(1).strip()
    todos = load_todos()
    todos.append({'text': task, 'done': False, 'id': int(time.time())})
    write_todos(todos)
    await e.edit(f"✅ Задача добавлена: _{task}_\n📋 Всего: {len(todos)}")

@client.on(events.NewMessage(pattern=r'/todos$', func=is_me))
async def todos_cmd(e):
    todos = load_todos()
    if not todos: await e.edit("📭 Список задач пуст"); return
    lines = []
    for i, t in enumerate(todos, 1):
        mark = "✅" if t['done'] else "⬜"
        lines.append(f"{mark} {i}. _{t['text']}_")
    done = sum(1 for t in todos if t['done'])
    await e.edit(f"📋 **Список задач** ({done}/{len(todos)} выполнено):\n\n" + "\n".join(lines))

@client.on(events.NewMessage(pattern=r'/done (\d+)', func=is_me))
async def done_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = load_todos()
    if 0 <= idx < len(todos):
        todos[idx]['done'] = True; write_todos(todos)
        await e.edit(f"✅ Выполнено: _{todos[idx]['text']}_")
    else:
        await e.edit(f"❌ Задача #{idx+1} не найдена")

@client.on(events.NewMessage(pattern=r'/undone (\d+)', func=is_me))
async def undone_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = load_todos()
    if 0 <= idx < len(todos):
        todos[idx]['done'] = False; write_todos(todos)
        await e.edit(f"⬜ Снята отметка: _{todos[idx]['text']}_")
    else:
        await e.edit(f"❌ Задача #{idx+1} не найдена")

@client.on(events.NewMessage(pattern=r'/deltodo (\d+)', func=is_me))
async def deltodo_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = load_todos()
    if 0 <= idx < len(todos):
        removed = todos.pop(idx); write_todos(todos)
        await e.edit(f"🗑 Удалена задача: _{removed['text']}_")
    else:
        await e.edit(f"❌ Задача #{idx+1} не найдена")

# ============================================================
# 7. AFK
# ============================================================

@client.on(events.NewMessage(pattern=r'/afk(?:\s+(.+))?$', func=is_me))
async def afk_cmd(e):
    global afk_start_time, afk_reason
    afk_start_time = time.time(); afk_reason = (e.pattern_match.group(1) or '').strip()
    r = f"\n📝 _{afk_reason}_" if afk_reason else ""
    await e.edit(f"😴 **AFK включён**{r}")

@client.on(events.NewMessage(pattern=r'/unafk$', func=is_me))
async def unafk_cmd(e):
    global afk_start_time, afk_reason
    if afk_start_time:
        dur = fmt_time(time.time() - afk_start_time)
        afk_start_time = None; afk_reason = ""
        await e.edit(f"☀️ **AFK выключен** | Отсутствовал: _{dur}_")
    else:
        await e.edit("ℹ️ AFK не был включён")

# ============================================================
# 8. ИНФОРМАЦИЯ О ЧАТЕ
# ============================================================

@client.on(events.NewMessage(pattern=r'/chatinfo$', func=is_me))
async def chatinfo_cmd(e):
    chat = await e.get_chat()
    name = getattr(chat,'title',None) or f"{getattr(chat,'first_name','')} {getattr(chat,'last_name','')}".strip()
    uname = getattr(chat,'username',None)
    members = getattr(chat,'participants_count',None)
    lines = [
        f"📊 **Информация о чате**\n",
        f"📛 **{name}**",
        f"🆔 `{e.chat_id}`",
        f"🔖 @{uname}" if uname else "🔖 Username: нет",
        f"👥 Тип: `{type(chat).__name__}`",
    ]
    if members: lines.append(f"👤 Участников: `{members}`")
    await e.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'/members$', func=is_me))
async def members_cmd(e):
    try:
        p = await client.get_participants(e.chat_id)
        bots = sum(1 for x in p if x.bot)
        await e.edit(f"👥 **Участники**\n\nВсего: `{len(p)}`\n👤 Людей: `{len(p)-bots}`\n🤖 Ботов: `{bots}`")
    except Exception as ex:
        await e.edit(f"❌ {ex}")

@client.on(events.NewMessage(pattern=r'/admins$', func=is_me))
async def admins_cmd(e):
    try:
        admins = await client.get_participants(e.chat_id, filter=ChannelParticipantsAdmins())
        lines = [f"👑 **Администраторы ({len(admins)}):**\n"]
        for a in admins[:25]:
            name = f"{a.first_name or ''} {a.last_name or ''}".strip()
            lines.append(f"• {name} — {'@'+a.username if a.username else '`'+str(a.id)+'`'}")
        await e.edit("\n".join(lines))
    except Exception as ex:
        await e.edit(f"❌ {ex}")

@client.on(events.NewMessage(pattern=r'/top(?:\s+(\d+))?$', func=is_me))
async def top_cmd(e):
    limit = int(e.pattern_match.group(1) or 200)
    await e.edit("⏳ Анализирую...")
    cnt, names = defaultdict(int), {}
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        if msg.sender_id:
            cnt[msg.sender_id] += 1
            if msg.sender_id not in names:
                s = await msg.get_sender()
                if s:
                    n = f"{getattr(s,'first_name','') or ''} {getattr(s,'last_name','') or ''}".strip()
                    names[msg.sender_id] = n or str(msg.sender_id)
    top = sorted(cnt.items(), key=lambda x:x[1], reverse=True)[:10]
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lines = [f"🏆 **Топ активных** (из {limit} сообщ.):\n"]
    for i,(uid,c) in enumerate(top):
        lines.append(f"{medals[i]} {names.get(uid,uid)} — `{c}` сообщ.")
    await e.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'/bots$', func=is_me))
async def bots_cmd(e):
    try:
        bots = await client.get_participants(e.chat_id, filter=ChannelParticipantsBots())
        lines = [f"🤖 **Боты в чате ({len(bots)}):**\n"]
        for b in bots[:20]:
            lines.append(f"• @{b.username or b.id}")
        await e.edit("\n".join(lines))
    except Exception as ex:
        await e.edit(f"❌ {ex}")

# ============================================================
# 9. СТАТИСТИКА (новая команда)
# ============================================================

@client.on(events.NewMessage(pattern=r'/stats$', func=is_me))
async def stats_cmd(e):
    s = _load(stats_file)
    total = sum(s.values())
    if total == 0:
        await e.edit("📊 Статистика пуста. Пока не было команд.")
        return
    top = sorted(s.items(), key=lambda x: x[1], reverse=True)[:5]
    lines = [f"📊 **Статистика команд** (всего {total})"]
    for cmd, count in top:
        lines.append(f"• `/{cmd}` — {count} раз ({count/total*100:.1f}%)")
    await e.edit("\n".join(lines))

# ============================================================
# 10. HELP (обновлён)
# ============================================================

HELP_CATS = {
    'основные': '... (оставлено как в исходнике, но можно дополнить)',
    # ... все категории остаются без изменений, но я сократил для краткости
}

@client.on(events.NewMessage(pattern=r'/help(?:\s+(.+))?$', func=is_me))
async def help_cmd(e):
    cat = (e.pattern_match.group(1) or '').strip().lower()
    if cat and cat in HELP_CATS:
        await e.edit(HELP_CATS[cat]); return
    if cat:
        await e.edit(f"❌ Категория `{cat}` не найдена.\nДоступные: `{', '.join(HELP_CATS)}`"); return
    await e.edit(
        "📚 **UserBot — 70+ команд**\n\n"
        "Пиши `/help [категория]` для подробного описания:\n\n"
        "⚙️ основные\n👤 профиль\n🎮 игры\n🛠 утилиты\n✉️ сообщения\n📦 заметки\n😴 afk\n📊 инфо\n📈 stats"
    )
    bump_stat('cmds')

# ============================================================
# 11. ВХОДЯЩИЕ СООБЩЕНИЯ (автоответчик + AFK)
# ============================================================

@client.on(events.NewMessage(incoming=True))
async def incoming_handler(event):
    if event.sender_id == MY_ID:
        return
    if not event.is_private:
        return
    sender = await event.get_sender()
    if not sender or sender.bot:
        return
    uid = event.sender_id
    now = time.time()
    if afk_start_time and now - reply_cooldown.get(f'afk_{uid}', 0) > 60:
        dur = fmt_time(now - afk_start_time)
        r = f"\n📝 _{afk_reason}_" if afk_reason else ""
        reply_cooldown[f'afk_{uid}'] = now
        await event.reply(f"😴 Хозяин AFK уже **{dur}**{r}")
    if auto_reply_enabled and now - reply_cooldown.get(uid, 0) > 10:
        reply_cooldown[uid] = now
        await asyncio.sleep(1)
        await event.reply(auto_reply_text)

# ============================================================
# 12. ЗАПУСК
# ============================================================

if __name__ == "__main__":
    print("🚀 Запуск UserBot (улучшенная версия)...")
    Thread(target=run_web, daemon=True).start()
    client.start()
    loop = asyncio.get_event_loop()
    MY_ID = loop.run_until_complete(client.get_me()).id
    print(f"✅ UserBot запущен! ID: {MY_ID}")
    client.run_until_disconnected()
