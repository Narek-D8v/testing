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
import unicodedata
from telethon import TelegramClient, events, utils
from telethon.tl.types import (
    InputMediaDice, MessageEntityMentionName,
    ChannelParticipantsAdmins, ChannelParticipantsBots,
    ReactionEmoji
)
from telethon.tl.functions.messages import GetHistoryRequest, SendReactionRequest
from telethon.tl.functions.account import UpdateProfileRequest
from flask import Flask
from threading import Thread
from collections import defaultdict

logging.basicConfig(level=logging.INFO)

api_id  = os.environ.get('API_ID')
api_hash = os.environ.get('API_HASH')

# ── глобальное состояние ───────────────────────────
auto_reply_enabled = False
auto_reply_text    = '💫 Я автоответчик, хозяин скоро ответит! Спасибо за терпение 😘'
afk_start_time     = None
afk_reason         = ""
ghost_mode         = False          # невидимый режим (удаляем команды мгновенно)
data_file          = 'userbot_data.json'
saved_file         = 'saved_data.json'
notes_file         = 'notes_data.json'
todos_file         = 'todos_data.json'
stats_file         = 'stats_data.json'
MY_ID              = None           # будет установлен после запуска

# ── загрузка / сохранение ─────────────────────────
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

# ── клиент ────────────────────────────────────────
try:
    client = TelegramClient('my_userbot', int(api_id), api_hash)
except ValueError as _e:
    if "too many values to unpack" in str(_e):
        from telethon.sessions import SQLiteSession
        client = TelegramClient(SQLiteSession('my_userbot'), int(api_id), api_hash)
    else:
        raise

app = Flask(__name__)
reply_cooldown = defaultdict(float)

@app.route('/')
def home():
    return "🤖 UserBot работает 24/7!"

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# ══════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════════════

async def safe_eval(expr: str):
    func_map = {
        'sqrt':'math.sqrt','sin':'math.sin','cos':'math.cos','tan':'math.tan',
        'log':'math.log','log2':'math.log2','log10':'math.log10',
        'abs':'abs','pow':'pow','floor':'math.floor','ceil':'math.ceil',
        'round':'round','pi':'math.pi','e':'math.e',
        'factorial':'math.factorial','gcd':'math.gcd','hypot':'math.hypot',
    }
    safe = expr.strip()
    for k, v in func_map.items():
        safe = re.sub(rf'\b{k}\b', v, safe)
    if any(w in safe for w in ['import','os','sys','open','exec','eval','__']):
        return None
    ns = {'__builtins__': {}, 'math': math, 'abs': abs, 'pow': pow, 'round': round}
    try:
        r = eval(safe, ns, {})
        if isinstance(r, float):
            if math.isinf(r) or math.isnan(r):
                return "∞"
            return round(r, 10)
        return r
    except:
        return None

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

def caesar(text, shift, dec=False):
    if dec: shift = -shift
    out = []
    for c in text:
        if 'А' <= c <= 'я' or c in 'ёЁ':
            base = ord('А' if c.isupper() or c == 'Ё' else 'а')
            size = 33
            out.append(chr((ord(c) - base + shift) % size + base))
        elif c.isalpha():
            base = ord('A' if c.isupper() else 'a')
            out.append(chr((ord(c) - base + shift) % 26 + base))
        else:
            out.append(c)
    return ''.join(out)

_MORSE = {
    'A':'.-','B':'-...','C':'-.-.','D':'-..','E':'.','F':'..-.','G':'--.','H':'....','I':'..','J':'.---',
    'K':'-.-','L':'.-..','M':'--','N':'-.','O':'---','P':'.--.','Q':'--.-','R':'.-.','S':'...','T':'-',
    'U':'..-','V':'...-','W':'.--','X':'-..-','Y':'-.--','Z':'--..',
    '0':'-----','1':'.----','2':'..---','3':'...--','4':'....-','5':'.....','6':'-....','7':'--...','8':'---..','9':'----.',
    ' ':'/'
}
def morse_enc(t): return ' '.join(_MORSE.get(c.upper(),'?') for c in t)

def gen_pwd(n=16, sym=True):
    pool = string.ascii_letters + string.digits + ("!@#$%^&*()-_=+[]{}|;:,.<>?" if sym else "")
    return ''.join(random.SystemRandom().choice(pool) for _ in range(n))

def vigenere(text, key, dec=False):
    key = key.upper()
    out, ki = [], 0
    for c in text:
        if c.isalpha():
            shift = ord(key[ki % len(key)]) - ord('A')
            if dec: shift = -shift
            base = ord('A' if c.isupper() else 'a')
            out.append(chr((ord(c) - base + shift) % 26 + base))
            ki += 1
        else:
            out.append(c)
    return ''.join(out)

def progress_bar(val, mx, width=10):
    filled = int(width * val / max(mx, 1))
    return "█" * filled + "░" * (width - filled)

async def send_reminder(chat_id, msg, delay):
    await asyncio.sleep(delay)
    await client.send_message(chat_id, f"⏰ **НАПОМИНАНИЕ:**\n{msg}")

bot_start = time.time()

# Фильтр для команд от меня
def is_me(event):
    return event.sender_id == MY_ID

# ══════════════════════════════════════════════════
#  1. ОСНОВНЫЕ
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/sleep$', func=is_me))
async def sleep_cmd(e):
    global auto_reply_enabled
    auto_reply_enabled = True
    save_data()
    await e.reply('💤 Автоответчик **ВКЛЮЧЕН**.')

@client.on(events.NewMessage(pattern=r'/wake$', func=is_me))
async def wake_cmd(e):
    global auto_reply_enabled
    auto_reply_enabled = False
    save_data()
    await e.reply('☀️ Автоответчик **ВЫКЛЮЧЕН**.')

@client.on(events.NewMessage(pattern=r'/setreply (.+)', func=is_me))
async def setreply_cmd(e):
    global auto_reply_text
    auto_reply_text = e.pattern_match.group(1).strip()
    save_data()
    await e.reply(f"✅ Текст автоответчика:\n_{auto_reply_text}_")

@client.on(events.NewMessage(pattern=r'/status$', func=is_me))
async def status_cmd(e):
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    s = _load(stats_file)
    await e.reply(
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
    await e.reply(
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
    msg = await e.reply("🏓 ...")
    ms = (time.monotonic() - t0) * 1000
    q  = "🟢 Отлично" if ms < 150 else "🟡 Нормально" if ms < 400 else "🔴 Высокая"
    await msg.edit(f"🏓 **Понг!**\n⚡ Задержка: `{ms:.1f} мс`\n📶 Качество: {q}")

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
    await e.reply("\n".join(lines))

@client.on(events.NewMessage(pattern=r'/info$', func=is_me))
async def info_cmd(e):
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    await e.reply(
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
    await e.reply('🔄 Перезагрузка...')
    await asyncio.sleep(2)
    await client.disconnect()
    os._exit(0)

@client.on(events.NewMessage(pattern=r'/ghost$', func=is_me))
async def ghost_cmd(e):
    global ghost_mode
    ghost_mode = not ghost_mode
    save_data()
    if ghost_mode:
        await e.reply("👻 **Ghost-режим ВКЛЮЧЁН** — команды удаляются мгновенно")
        await asyncio.sleep(2)
        await e.delete()
    else:
        await e.reply("👁 **Ghost-режим ВЫКЛЮЧЕН**")

# ══════════════════════════════════════════════════
#  2. ПРОФИЛЬ
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/me$', func=is_me))
async def me_cmd(e):
    me = await client.get_me()
    photos = await client.get_profile_photos(me.id, limit=1)
    await e.reply(
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
        await e.reply(file=photos[0])
        await e.delete()
    else:
        await e.reply("❌ Аватарка не найдена")

@client.on(events.NewMessage(pattern=r'/name (.+)', func=is_me))
async def name_cmd(e):
    n = e.pattern_match.group(1).strip()
    await client.edit_profile(first_name=n)
    await e.reply(f"✅ Имя → **{n}**")

@client.on(events.NewMessage(pattern=r'/lastname(?:\s+(.+))?$', func=is_me))
async def lastname_cmd(e):
    n = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(last_name=n)
    await e.reply(f"✅ Фамилия → **{n}**" if n else "✅ Фамилия удалена")

@client.on(events.NewMessage(pattern=r'/bio(?:\s+(.+))?$', func=is_me))
async def bio_cmd(e):
    t = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(about=t)
    await e.reply(f"✅ Био → _{t}_" if t else "✅ Био очищено")

@client.on(events.NewMessage(pattern=r'/whois (.+)', func=is_me))
async def whois_cmd(e):
    target = e.pattern_match.group(1).strip().lstrip('@')
    try:
        ent  = await client.get_entity(target)
        name = f"{getattr(ent,'first_name','') or ''} {getattr(ent,'last_name','') or ''}".strip() \
               or getattr(ent,'title','?')
        uname = f"@{ent.username}" if getattr(ent,'username',None) else "нет"
        bot_  = "✅" if getattr(ent,'bot',False) else "❌"
        ver   = "✅" if getattr(ent,'verified',False) else "❌"
        await e.reply(
            f"🔍 **Информация о пользователе**\n\n"
            f"📛 Имя: **{name}**\n"
            f"🆔 ID: `{ent.id}`\n"
            f"🔰 Username: {uname}\n"
            f"🤖 Бот: {bot_}\n"
            f"✔️ Verified: {ver}"
        )
    except Exception as ex:
        await e.reply(f"❌ Не найден: {ex}")

@client.on(events.NewMessage(pattern=r'/username_check (.+)', func=is_me))
async def username_check_cmd(e):
    uname = e.pattern_match.group(1).strip().lstrip('@')
    try:
        ent = await client.get_entity(uname)
        name = getattr(ent,'first_name',None) or getattr(ent,'title','?')
        await e.reply(f"🔍 @{uname}\n✅ **Занят**\n👤 {name}\n🆔 `{ent.id}`")
    except:
        await e.reply(f"🔍 @{uname}\n✅ **Свободен**")

# ══════════════════════════════════════════════════
#  3. ИГРЫ И РАЗВЛЕЧЕНИЯ
# ══════════════════════════════════════════════════

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
    await e.reply(f"🪙 Монета вращается {flips} раз...\n\nРезультат: **{r}**")

@client.on(events.NewMessage(pattern=r'/rand(?:\s+(-?\d+)(?:\s+(-?\d+))?)?$', func=is_me))
async def rand_cmd(e):
    g = e.pattern_match
    a, b = g.group(1), g.group(2)
    if a and b:
        lo, hi = sorted([int(a), int(b)])
        await e.reply(f"🎲 `{lo}` … `{hi}` → **{random.randint(lo, hi)}**")
    elif a:
        await e.reply(f"🎲 `1` … `{a}` → **{random.randint(1, int(a))}**")
    else:
        await e.reply(f"🎲 **{random.randint(1, 100)}**")

# ── улучшенный магический шар ─────────────────────
@client.on(events.NewMessage(pattern=r'/8ball(?:\s+(.+))?$', func=is_me))
async def eightball_cmd(e):
    ANSWERS = {
        'pos': [
            ("Определённо да",        "✅", "Вселенная согласна с тобой."),
            ("Без сомнений",           "💯", "Это решено раньше, чем ты спросил."),
            ("Скорее всего да",        "👍", "Всё складывается в твою пользу."),
            ("Хорошие перспективы",    "🌟", "Будущее выглядит светлым."),
            ("Знаки говорят «да»",     "🔮", "Мистические силы на твоей стороне."),
            ("Всё указывает на «да»",  "💫", "Судьба уже всё решила."),
            ("Да, и поскорее",         "🚀", "Не медли — действуй прямо сейчас."),
            ("Абсолютно точно",        "🏆", "Лучшего ответа не существует."),
            ("Это неизбежно",          "⚡", "Ничто не остановит это."),
            ("Да, если сделаешь шаг",  "🦶", "Действие — ключ к результату."),
            ("Вселенная шепчет: да",   "🌌", "Даже звёзды кивают."),
            ("Смело иди вперёд",       "🎯", "Ты уже знал ответ — я лишь подтверждаю."),
        ],
        'neu': [
            ("Пока не ясно",             "🤔", "Туман будущего слишком густой."),
            ("Спроси позже",             "⏰", "Момент ещё не настал."),
            ("Не могу предсказать",      "🌫", "Слишком много переменных."),
            ("Сосредоточься и повтори",  "🧘", "Твой разум мешает ответу."),
            ("Лучше не рассказывать",    "🤫", "Некоторые тайны лучше хранить."),
            ("Трудно сказать",           "😶", "Даже я не всесилен."),
            ("Возможно, но не сейчас",   "🌙", "Подожди подходящего момента."),
            ("Ответ где-то рядом",       "🔭", "Смотри внимательнее вокруг себя."),
        ],
        'neg': [
            ("Мой ответ — нет",          "🚫", "Прими это спокойно."),
            ("Перспективы не очень",     "😕", "Стоит пересмотреть планы."),
            ("Весьма сомнительно",       "🙄", "Интуиция говорит «осторожно»."),
            ("Точно нет",                "💀", "Даже не думай об этом."),
            ("Не рассчитывай",           "❌", "Лучше найди другой путь."),
            ("Категорически нет",        "🔴", "Вселенная против."),
            ("Всё против этого",         "⛈", "Сейчас не лучшее время."),
            ("Откажись от идеи",         "🗑", "Это дорога в никуда."),
            ("Шансы ничтожны",           "🎰", "Даже удача отвернулась."),
        ],
    }

    question = (e.pattern_match.group(1) or '').strip()

    # анимация вращения
    spin = ["🎱", "🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘", "🎱"]
    msg = await e.reply("🎱 Шар вращается...")
    for frame in spin:
        await msg.edit(f"{frame} Шар вращается...")
        await asyncio.sleep(0.15)

    pool_key = random.choices(['pos','neu','neg'], weights=[38,27,35])[0]
    answer, emoji, comment = random.choice(ANSWERS[pool_key])
    color = {"pos":"🟢","neu":"🟡","neg":"🔴"}[pool_key]
    label = {"pos":"ПОЗИТИВНЫЙ","neu":"НЕЙТРАЛЬНЫЙ","neg":"НЕГАТИВНЫЙ"}[pool_key]
    confidence = random.randint(55, 99)
    bar = progress_bar(confidence, 100, 10)

    q_line = f"❓ _{question}_\n\n" if question else ""
    await msg.edit(
        f"🎱 **Магический шар**\n\n"
        f"{q_line}"
        f"{'─'*22}\n"
        f"{emoji}  **{answer}**\n"
        f"{'─'*22}\n\n"
        f"💬 _{comment}_\n\n"
        f"{color} {label}\n"
        f"[{bar}] **{confidence}%** уверенности"
    )
    bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'/rps(?:\s+(.+))?$', func=is_me))
async def rps_cmd(e):
    MAP = {'к':'🪨 Камень','камень':'🪨 Камень','н':'✂️ Ножницы','ножницы':'✂️ Ножницы','б':'📄 Бумага','бумага':'📄 Бумага'}
    BOT = ['🪨 Камень','✂️ Ножницы','📄 Бумага']
    WIN = {'🪨 Камень':'✂️ Ножницы','✂️ Ножницы':'📄 Бумага','📄 Бумага':'🪨 Камень'}
    arg = (e.pattern_match.group(1) or '').lower().strip()
    if not arg or arg not in MAP:
        await e.reply("✊✌️🖐 `/rps камень` / `ножницы` / `бумага` (или `к`/`н`/`б`)")
        return
    uc, bc = MAP[arg], random.choice(BOT)
    if uc == bc:   res = "🤝 **Ничья!**"
    elif WIN[uc]==bc: res = "🏆 **Ты победил!**"
    else:          res = "💀 **Бот победил!**"
    await e.reply(f"✊✌️🖐 **КНБ**\n\n👤 Ты: {uc}\n🤖 Бот: {bc}\n\n{res}")

@client.on(events.NewMessage(pattern=r'/slot$', func=is_me))
async def slot_cmd(e):
    SYM = ['🍒','🍋','🍊','🍇','🍉','⭐','💎','7️⃣','🔔','🍀']
    # анимация
    msg = await e.reply("🎰 [ ▓ | ▓ | ▓ ]")
    for _ in range(4):
        s = [random.choice(SYM) for _ in range(3)]
        await msg.edit(f"🎰 [ {s[0]} | {s[1]} | {s[2]} ]")
        await asyncio.sleep(0.3)
    s = [random.choice(SYM) for _ in range(3)]
    if s[0]==s[1]==s[2]:
        res = "💰💰💰 **ДЖЕКПОТ!**" if s[0] in ('💎','7️⃣') else "🎊 **Выигрыш! Три одинаковых!**"
    elif len(set(s))<3:
        res = "😅 Почти! Два одинаковых — ещё раз!"
    else:
        res = "💸 Не повезло. Попробуй снова!"
    await msg.edit(f"🎰 [ {s[0]} | {s[1]} | {s[2]} ]\n\n{res}")

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
    await e.reply(f"🔮 **Индекс удачи**\n\n[{bar}] **{pct}%**\n\n{msg}")

@client.on(events.NewMessage(pattern=r'/choose (.+)', func=is_me))
async def choose_cmd(e):
    raw = e.pattern_match.group(1)
    opts = [o.strip() for o in re.split(r'[,|/]', raw) if o.strip()]
    if len(opts) < 2:
        await e.reply("ℹ️ Перечисли варианты через запятую: `/choose пицца, суши, бургер`")
        return
    winner = random.choice(opts)
    listed = "\n".join(f"{'➡️' if o==winner else '  •'} {o}" for o in opts)
    await e.reply(f"🤔 **Выбираю из {len(opts)} вариантов...**\n\n{listed}\n\n✅ **Выбор: {winner}**")

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
    await e.reply(
        f"🧠 **Вопрос:**\n_{q}_\n\n{opts_text}\n\n"
        f"||✅ Ответ: **{correct}**||"
    )

# ══════════════════════════════════════════════════
#  4. УТИЛИТЫ
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/calc (.+)', func=is_me))
async def calc_cmd(e):
    expr = e.pattern_match.group(1).strip()
    r = await safe_eval(expr)
    if r is not None:
        await e.reply(f"🧮 `{expr}` = **{r}**")
    else:
        await e.reply("❌ Ошибка выражения. Разрешены: `+ - * / % sqrt sin cos tan log abs pow pi e factorial ceil floor round`")

@client.on(events.NewMessage(pattern=r'/remind (\d+)\s+(.+)', func=is_me))
async def remind_cmd(e):
    delay = int(e.pattern_match.group(1))
    text  = e.pattern_match.group(2).strip()
    await e.reply(f"⏰ Напоминание через **{fmt_time(delay)}**\n📝 _{text}_")
    asyncio.create_task(send_reminder(e.chat_id, text, delay))

@client.on(events.NewMessage(pattern=r'/search (.+)', func=is_me))
async def search_cmd(e):
    q = e.pattern_match.group(1).strip()
    enc = q.replace(' ','+')
    await e.reply(
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
    msg = await e.reply("⏳ Сокращаю...")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://tinyurl.com/api-create.php?url={url}",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                short = await r.text()
        if short.startswith('http'):
            await msg.edit(f"✂️ **Оригинал:** `{url[:55]}{'…' if len(url)>55 else ''}`\n🔗 **Короткая:** {short.strip()}")
        else:
            raise Exception()
    except:
        await msg.edit("❌ Ошибка. Проверь URL.")

@client.on(events.NewMessage(pattern=r'/weather (.+)', func=is_me))
async def weather_cmd(e):
    city = e.pattern_match.group(1).strip()
    enc  = city.replace(' ','+')
    await e.reply(
        f"🌤️ **Погода: {city}**\n\n"
        f"• [wttr.in](https://wttr.in/{enc})\n"
        f"• [OpenWeatherMap](https://openweathermap.org/find?q={enc})\n"
        f"• [Weather.com](https://weather.com/ru-RU/weather/today/l/{enc})"
    )

@client.on(events.NewMessage(pattern=r'/translate (.+)', func=is_me))
async def translate_cmd(e):
    text = e.pattern_match.group(1).strip()
    enc  = text.replace(' ','%20')
    await e.reply(
        f"🌐 **Перевод:** _{text}_\n\n"
        f"• [RU→EN](https://translate.google.com/?sl=ru&tl=en&text={enc})\n"
        f"• [EN→RU](https://translate.google.com/?sl=en&tl=ru&text={enc})\n"
        f"• [Auto→RU](https://translate.google.com/?sl=auto&tl=ru&text={enc})"
    )

@client.on(events.NewMessage(pattern=r'/base64 (encode|decode) (.+)', func=is_me))
async def base64_cmd(e):
    mode, text = e.pattern_match.group(1), e.pattern_match.group(2).strip()
    try:
        if mode == 'encode':
            res = base64.b64encode(text.encode()).decode()
            await e.reply(f"🔐 **Base64 encode:**\n`{res}`")
        else:
            res = base64.b64decode(text.encode()).decode()
            await e.reply(f"🔓 **Base64 decode:**\n`{res}`")
    except:
        await e.reply("❌ Ошибка. Проверь данные.")

@client.on(events.NewMessage(pattern=r'/hash (.+)', func=is_me))
async def hash_cmd(e):
    text = e.pattern_match.group(1).strip().encode()
    await e.reply(
        f"#️⃣ **Хэши**\n\n"
        f"MD5:    `{hashlib.md5(text).hexdigest()}`\n"
        f"SHA1:   `{hashlib.sha1(text).hexdigest()}`\n"
        f"SHA256: `{hashlib.sha256(text).hexdigest()}`\n"
        f"SHA512: `{hashlib.sha512(text).hexdigest()[:64]}…`"
    )

@client.on(events.NewMessage(pattern=r'/morse (.+)', func=is_me))
async def morse_cmd(e):
    text = e.pattern_match.group(1).strip()
    await e.reply(f"📡 **Морзе:**\n_{text}_\n\n`{morse_enc(text)}`")

@client.on(events.NewMessage(pattern=r'/caesar (encode|decode) (\d+) (.+)', func=is_me))
async def caesar_cmd(e):
    mode, shift, text = e.pattern_match.group(1), int(e.pattern_match.group(2)), e.pattern_match.group(3)
    res = caesar(text, shift, dec=(mode=='decode'))
    await e.reply(f"{'🔒' if mode=='encode' else '🔓'} **Цезарь (сдвиг {shift}):**\n_{text}_\n\n`{res}`")

@client.on(events.NewMessage(pattern=r'/vigenere (encode|decode) (\S+) (.+)', func=is_me))
async def vigenere_cmd(e):
    mode, key, text = e.pattern_match.group(1), e.pattern_match.group(2), e.pattern_match.group(3)
    res = vigenere(text, key, dec=(mode=='decode'))
    await e.reply(f"{'🔒' if mode=='encode' else '🔓'} **Виженер (ключ: {key}):**\n_{text}_\n\n`{res}`")

@client.on(events.NewMessage(pattern=r'/password(?:\s+(\d+))?(?:\s+(simple))?$', func=is_me))
async def password_cmd(e):
    length = max(4, min(int(e.pattern_match.group(1) or 16), 128))
    sym    = not e.pattern_match.group(2)
    pwd    = gen_pwd(length, sym)
    s = "🔴 Слабый" if length<8 else "🟡 Средний" if length<12 else "🟢 Сильный" if length<20 else "💎 Очень сильный"
    await e.reply(f"🔑 **Пароль ({length} симв.)**\n\n`{pwd}`\n\nСила: {s}\nСимволы: {'✅' if sym else '❌'}")

@client.on(events.NewMessage(pattern=r'/qr (.+)', func=is_me))
async def qr_cmd(e):
    text = e.pattern_match.group(1).strip().replace(' ','+')
    await e.reply(
        f"📱 **QR-код**\n\n"
        f"🔗 [Открыть изображение](https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={text})"
    )

@client.on(events.NewMessage(pattern=r'/uuid$', func=is_me))
async def uuid_cmd(e):
    import uuid
    ids = [str(uuid.uuid4()) for _ in range(5)]
    out = "\n".join(f"`{u}`" for u in ids)
    await e.reply(f"🆔 **Случайные UUID v4:**\n\n{out}")

@client.on(events.NewMessage(pattern=r'/color (#[0-9a-fA-F]{6}|\d+,\d+,\d+)', func=is_me))
async def color_cmd(e):
    raw = e.pattern_match.group(1).strip()
    if raw.startswith('#'):
        h = raw.lstrip('#')
        r,g,b = int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
        hex_val = raw.upper()
    else:
        r,g,b = map(int,raw.split(','))
        hex_val = f"#{r:02X}{g:02X}{b:02X}"
    # HSL
    rf,gf,bf = r/255,g/255,b/255
    mx,mn = max(rf,gf,bf),min(rf,gf,bf)
    l = (mx+mn)/2
    s_val = 0 if mx==mn else (mx-mn)/(1-abs(2*l-1))
    if mx==mn: h_val=0
    elif mx==rf: h_val=60*((gf-bf)/(mx-mn)%6)
    elif mx==gf: h_val=60*((bf-rf)/(mx-mn)+2)
    else: h_val=60*((rf-gf)/(mx-mn)+4)
    await e.reply(
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
    await e.reply(f"🔢 **ASCII коды:**\n_{text}_\n\n`{codes}`\n\nОбратно: `{back}`")

# ══════════════════════════════════════════════════
#  5. УПРАВЛЕНИЕ СООБЩЕНИЯМИ
# ══════════════════════════════════════════════════

# ── улучшенный /type ──────────────────────────────
@client.on(events.NewMessage(pattern=r'/type(?:\s+(fast|slow|matrix|glitch))?\s+(.+)', func=is_me))
async def type_cmd(e):
    mode = e.pattern_match.group(1) or 'normal'
    text = e.pattern_match.group(2).strip()

    if mode == 'fast':
        # быстрая печать — по 2 символа сразу
        msg = await e.reply("▌")
        for i in range(0, len(text), 2):
            chunk = text[:i+2]
            await msg.edit(chunk + ("▌" if i+2 < len(text) else ""))
            await asyncio.sleep(0.04)
        await msg.edit(text)

    elif mode == 'slow':
        # медленная — с паузами на знаках препинания
        msg = await e.reply("▌")
        shown = ""
        for ch in text:
            shown += ch
            await msg.edit(shown + "▌")
            pause = 0.3 if ch in '.!?…' else 0.12 if ch in ',;:' else 0.07
            await asyncio.sleep(pause)
        await msg.edit(text)

    elif mode == 'matrix':
        # матрица — символы «падают» и проявляется итоговый текст
        CHARS = string.ascii_letters + string.digits + "@#%&"
        msg = await e.reply("▓" * len(text))
        for step in range(len(text)):
            parts = list(text[:step])
            for _ in range(len(text) - step):
                parts.append(random.choice(CHARS))
            await msg.edit(''.join(parts))
            await asyncio.sleep(0.07)
        await msg.edit(text)

    elif mode == 'glitch':
        # глич — текст мигает и «ломается» перед появлением
        GLITCH = "░▒▓█▄▀■□▪▫"
        msg = await e.reply("".join(random.choice(GLITCH) for _ in text))
        for _ in range(6):
            glitched = "".join(
                c if random.random() > 0.4 else random.choice(GLITCH)
                for c in text
            )
            await msg.edit(glitched)
            await asyncio.sleep(0.12)
        await msg.edit(text)

    else:
        # обычный режим
        msg = await e.reply("▌")
        shown = ""
        for i, ch in enumerate(text):
            shown += ch
            if i % 2 == 0 or i == len(text)-1:
                await msg.edit(shown + ("▌" if i < len(text)-1 else ""))
                await asyncio.sleep(0.05)
        await msg.edit(text)

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
    await e.delete()
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        if msg.out or (msg.from_id and getattr(msg.from_id,'user_id',None)==my_id):
            await msg.delete(); count += 1; await asyncio.sleep(0.1)
    info = await client.send_message(e.chat_id, f"✅ Удалено **{count}** своих сообщений")
    await asyncio.sleep(3); await info.delete()

@client.on(events.NewMessage(pattern=r'/purge(?:\s+(\d+))?$', func=is_me))
async def purge_cmd(e):
    limit = int(e.pattern_match.group(1) or 10)
    await e.delete()
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        await msg.delete(); count += 1; await asyncio.sleep(0.04)
    info = await client.send_message(e.chat_id, f"⚠️ Удалено **{count}** сообщений")
    await asyncio.sleep(3); await info.delete()

@client.on(events.NewMessage(pattern=r'/spam (\d+) (.+)', func=is_me))
async def spam_cmd(e):
    count, text = int(e.pattern_match.group(1)), e.pattern_match.group(2).strip()
    await e.delete()
    for _ in range(count):
        await client.send_message(e.chat_id, text)
        await asyncio.sleep(0.35)

@client.on(events.NewMessage(pattern=r'/forward (-?\d+)', func=is_me))
async def forward_cmd(e):
    if not e.reply_to_msg_id:
        await e.reply("ℹ️ Ответьте на сообщение: `/forward [chat_id]`"); return
    try:
        msg = await e.get_reply_message()
        await client.forward_messages(int(e.pattern_match.group(1)), msg)
        await e.reply(f"✅ Переслано в `{e.pattern_match.group(1)}`")
    except Exception as ex:
        await e.reply(f"❌ {ex}")

@client.on(events.NewMessage(pattern=r'/pin$', func=is_me))
async def pin_cmd(e):
    if not e.reply_to_msg_id:
        await e.reply("ℹ️ Ответьте на сообщение"); return
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
    msg = await e.reply(f"⏳ Копирую {count} сообщений...")
    msgs = []
    async for m in client.iter_messages(e.chat_id, limit=count):
        msgs.append(m)
    msgs.reverse()
    copied = 0
    for m in msgs:
        try:
            await client.forward_messages(target, m); copied += 1; await asyncio.sleep(0.4)
        except: pass
    await msg.edit(f"✅ Скопировано **{copied}/{count}** → `{target}`")

@client.on(events.NewMessage(pattern=r'/react (.+)', func=is_me))
async def react_cmd(e):
    """Ставит реакцию на сообщение (ответом)"""
    if not e.reply_to_msg_id:
        await e.reply("ℹ️ Ответьте на сообщение: `/react 👍`"); return
    emoji = e.pattern_match.group(1).strip()
    try:
        await client(SendReactionRequest(
            peer=e.chat_id,
            msg_id=e.reply_to_msg_id,
            reaction=[ReactionEmoji(emoticon=emoji)]
        ))
        await e.delete()
    except Exception as ex:
        await e.reply(f"❌ Не удалось поставить реакцию: {ex}")

# ══════════════════════════════════════════════════
#  6. ЗАМЕТКИ И TODO
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/save (\S+) (.+)', func=is_me))
async def save_cmd(e):
    k, v = e.pattern_match.group(1), e.pattern_match.group(2)
    d = _load(saved_file); d[k] = v; _write(saved_file, d)
    await e.reply(f"✅ `{k}` = _{v}_")

@client.on(events.NewMessage(pattern=r'/get (\S+)', func=is_me))
async def get_cmd(e):
    k = e.pattern_match.group(1)
    v = _load(saved_file).get(k)
    await e.reply(f"📦 `{k}` = _{v}_" if v else f"❌ Ключ `{k}` не найден")

@client.on(events.NewMessage(pattern=r'/del (\S+)', func=is_me))
async def del_cmd(e):
    k = e.pattern_match.group(1); d = _load(saved_file)
    if k in d:
        del d[k]; _write(saved_file, d); await e.reply(f"🗑 Удалено: `{k}`")
    else:
        await e.reply(f"❌ `{k}` не найден")

@client.on(events.NewMessage(pattern=r'/list$', func=is_me))
async def list_cmd(e):
    d = _load(saved_file)
    if not d: await e.reply("📭 Нет данных"); return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v)>40 else ''}_" for k,v in d.items())
    await e.reply(f"📦 **Сохранено ({len(d)}):**\n\n{items}")

@client.on(events.NewMessage(pattern=r'/note (\S+)(?: (.+))?', func=is_me))
async def note_cmd(e):
    k = e.pattern_match.group(1)
    t = e.pattern_match.group(2) or ""
    if e.reply_to_msg_id:
        r = await e.get_reply_message(); t = r.text or t
    if not t: await e.reply("ℹ️ `/note <название> <текст>` или ответом"); return
    d = _load(notes_file); d[k] = t; _write(notes_file, d)
    await e.reply(f"📝 Заметка сохранена: `{k}`")

@client.on(events.NewMessage(pattern=r'/getnote (\S+)', func=is_me))
async def getnote_cmd(e):
    k = e.pattern_match.group(1); d = _load(notes_file)
    await e.reply(f"📝 **{k}:**\n\n{d[k]}" if k in d else f"❌ Заметка `{k}` не найдена")

@client.on(events.NewMessage(pattern=r'/delnote (\S+)', func=is_me))
async def delnote_cmd(e):
    k = e.pattern_match.group(1); d = _load(notes_file)
    if k in d:
        del d[k]; _write(notes_file, d); await e.reply(f"🗑 Заметка удалена: `{k}`")
    else:
        await e.reply(f"❌ `{k}` не найдена")

@client.on(events.NewMessage(pattern=r'/notes$', func=is_me))
async def notes_cmd(e):
    d = _load(notes_file)
    if not d: await e.reply("📭 Нет заметок"); return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v)>40 else ''}_" for k,v in d.items())
    await e.reply(f"📝 **Заметки ({len(d)}):**\n\n{items}")

# ── TODO список ────────────────────────────────────
@client.on(events.NewMessage(pattern=r'/todo (.+)', func=is_me))
async def todo_add_cmd(e):
    task = e.pattern_match.group(1).strip()
    todos = load_todos()
    todos.append({'text': task, 'done': False, 'id': int(time.time())})
    write_todos(todos)
    await e.reply(f"✅ Задача добавлена: _{task}_\n📋 Всего: {len(todos)}")

@client.on(events.NewMessage(pattern=r'/todos$', func=is_me))
async def todos_cmd(e):
    todos = load_todos()
    if not todos: await e.reply("📭 Список задач пуст"); return
    lines = []
    for i, t in enumerate(todos, 1):
        mark = "✅" if t['done'] else "⬜"
        lines.append(f"{mark} {i}. _{t['text']}_")
    done = sum(1 for t in todos if t['done'])
    await e.reply(f"📋 **Список задач** ({done}/{len(todos)} выполнено):\n\n" + "\n".join(lines))

@client.on(events.NewMessage(pattern=r'/done (\d+)', func=is_me))
async def done_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = load_todos()
    if 0 <= idx < len(todos):
        todos[idx]['done'] = True; write_todos(todos)
        await e.reply(f"✅ Выполнено: _{todos[idx]['text']}_")
    else:
        await e.reply(f"❌ Задача #{idx+1} не найдена")

@client.on(events.NewMessage(pattern=r'/undone (\d+)', func=is_me))
async def undone_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = load_todos()
    if 0 <= idx < len(todos):
        todos[idx]['done'] = False; write_todos(todos)
        await e.reply(f"⬜ Снята отметка: _{todos[idx]['text']}_")
    else:
        await e.reply(f"❌ Задача #{idx+1} не найдена")

@client.on(events.NewMessage(pattern=r'/deltodo (\d+)', func=is_me))
async def deltodo_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = load_todos()
    if 0 <= idx < len(todos):
        removed = todos.pop(idx); write_todos(todos)
        await e.reply(f"🗑 Удалена задача: _{removed['text']}_")
    else:
        await e.reply(f"❌ Задача #{idx+1} не найдена")

# ══════════════════════════════════════════════════
#  7. AFK
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/afk(?:\s+(.+))?$', func=is_me))
async def afk_cmd(e):
    global afk_start_time, afk_reason
    afk_start_time = time.time(); afk_reason = (e.pattern_match.group(1) or '').strip()
    r = f"\n📝 _{afk_reason}_" if afk_reason else ""
    await e.reply(f"😴 **AFK включён**{r}")

@client.on(events.NewMessage(pattern=r'/unafk$', func=is_me))
async def unafk_cmd(e):
    global afk_start_time, afk_reason
    if afk_start_time:
        dur = fmt_time(time.time() - afk_start_time)
        afk_start_time = None; afk_reason = ""
        await e.reply(f"☀️ **AFK выключен** | Отсутствовал: _{dur}_")
    else:
        await e.reply("ℹ️ AFK не был включён")

# ══════════════════════════════════════════════════
#  8. ИНФОРМАЦИЯ О ЧАТЕ
# ══════════════════════════════════════════════════

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
    await e.reply("\n".join(lines))

@client.on(events.NewMessage(pattern=r'/members$', func=is_me))
async def members_cmd(e):
    try:
        p = await client.get_participants(e.chat_id)
        bots = sum(1 for x in p if x.bot)
        await e.reply(f"👥 **Участники**\n\nВсего: `{len(p)}`\n👤 Людей: `{len(p)-bots}`\n🤖 Ботов: `{bots}`")
    except Exception as ex:
        await e.reply(f"❌ {ex}")

@client.on(events.NewMessage(pattern=r'/admins$', func=is_me))
async def admins_cmd(e):
    try:
        admins = await client.get_participants(e.chat_id, filter=ChannelParticipantsAdmins())
        lines = [f"👑 **Администраторы ({len(admins)}):**\n"]
        for a in admins[:25]:
            name = f"{a.first_name or ''} {a.last_name or ''}".strip()
            lines.append(f"• {name} — {'@'+a.username if a.username else '`'+str(a.id)+'`'}")
        await e.reply("\n".join(lines))
    except Exception as ex:
        await e.reply(f"❌ {ex}")

@client.on(events.NewMessage(pattern=r'/top(?:\s+(\d+))?$', func=is_me))
async def top_cmd(e):
    limit = int(e.pattern_match.group(1) or 200)
    msg = await e.reply("⏳ Анализирую...")
    cnt, names = defaultdict(int), {}
    async for msg_obj in client.iter_messages(e.chat_id, limit=limit):
        if msg_obj.sender_id:
            cnt[msg_obj.sender_id] += 1
            if msg_obj.sender_id not in names:
                s = await msg_obj.get_sender()
                if s:
                    n = f"{getattr(s,'first_name','') or ''} {getattr(s,'last_name','') or ''}".strip()
                    names[msg_obj.sender_id] = n or str(msg_obj.sender_id)
    top = sorted(cnt.items(), key=lambda x:x[1], reverse=True)[:10]
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lines = [f"🏆 **Топ активных** (из {limit} сообщ.):\n"]
    for i,(uid,c) in enumerate(top):
        lines.append(f"{medals[i]} {names.get(uid,uid)} — `{c}` сообщ.")
    await msg.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'/bots$', func=is_me))
async def bots_cmd(e):
    try:
        bots = await client.get_participants(e.chat_id, filter=ChannelParticipantsBots())
        lines = [f"🤖 **Боты в чате ({len(bots)}):**\n"]
        for b in bots[:20]:
            lines.append(f"• @{b.username or b.id}")
        await e.reply("\n".join(lines))
    except Exception as ex:
        await e.reply(f"❌ {ex}")

# ══════════════════════════════════════════════════
#  /help — ПОЛНАЯ СПРАВКА
# ══════════════════════════════════════════════════

HELP_CATS = {
    'основные': (
        "⚙️ **ОСНОВНЫЕ (9 команд)**\n\n"
        "`/sleep` — Включить автоответчик. Бот будет отвечать в личку за тебя.\n\n"
        "`/wake` — Выключить автоответчик.\n\n"
        "`/setreply [текст]` — Задать свой текст автоответчика.\n"
        "   Пример: `/setreply Занят, отвечу позже`\n\n"
        "`/status` — Полный статус: аптайм, автоответчик, AFK, ghost-режим, статистика.\n\n"
        "`/time` — Время, UTC, дата, день недели, номер недели и день года.\n\n"
        "`/ping` — Задержка соединения с Telegram в мс + оценка качества.\n\n"
        "`/id` — ID чата и свой ID. Ответом — ID отправителя и сообщения.\n\n"
        "`/info` — Профиль бота: имя, ID, username, телефон, аптайм.\n\n"
        "`/restart` — Перезапуск бота через 2 секунды.\n\n"
        "`/ghost` — Переключить ghost-режим (команды удаляются мгновенно)."
    ),
    'профиль': (
        "👤 **ПРОФИЛЬ (7 команд)**\n\n"
        "`/me` — Свой профиль: имя, ID, username, телефон, аватар, verified.\n\n"
        "`/avatar` — Отправить свою аватарку. Ответом — аватарку другого пользователя.\n\n"
        "`/name [имя]` — Сменить имя в Telegram.\n"
        "   Пример: `/name Алексей`\n\n"
        "`/lastname [фамилия]` — Сменить фамилию. Без аргумента — удалить фамилию.\n\n"
        "`/bio [текст]` — Обновить «о себе». Без аргумента — очистить.\n\n"
        "`/whois @ник` — Полная информация о пользователе или канале по username/ID.\n\n"
        "`/username_check @ник` — Проверить занятость username. Показывает владельца если занят."
    ),
    'игры': (
        "🎮 **ИГРЫ И РАЗВЛЕЧЕНИЯ (12 команд)**\n\n"
        "`/dice` — Кубик 🎲 (анимация Telegram)\n"
        "`/dart` — Дротик 🎯\n"
        "`/basket` — Баскетбол 🏀\n"
        "`/football` — Футбол ⚽\n"
        "`/bowling` — Боулинг 🎳\n"
        "`/casino` — Слоты 🎰 (анимация Telegram)\n\n"
        "`/coin` — Монетка: орёл или решка.\n\n"
        "`/rand` — Число 1–100. `/rand [макс]` — до макс. `/rand [мин] [макс]` — диапазон.\n\n"
        "`/8ball [вопрос]` — Магический шар 🎱. Анимация вращения, 29 ответов в 3 категориях, процент уверенности и прогресс-бар.\n\n"
        "`/rps [к/н/б]` — Камень, ножницы, бумага против бота.\n"
        "   Пример: `/rps камень`\n\n"
        "`/slot` — Слот-машина с анимацией. 10 символов, джекпот за 💎 или 7️⃣.\n\n"
        "`/lucky` — Индекс удачи с прогресс-баром и советом дня.\n\n"
        "`/choose [вар1, вар2, ...]` — Выбрать случайный вариант из списка.\n"
        "   Пример: `/choose пицца, суши, бургер`\n\n"
        "`/quiz` — Случайный вопрос с вариантами и скрытым ответом."
    ),
    'утилиты': (
        "🛠 **УТИЛИТЫ (14 команд)**\n\n"
        "`/calc [выражение]` — Калькулятор. Функции: `sqrt sin cos tan log log2 log10 abs pow floor ceil round factorial gcd hypot pi e`.\n"
        "   Пример: `/calc factorial(10) / sqrt(pi)`\n\n"
        "`/remind [сек] [текст]` — Напоминание через N секунд. Без ограничений.\n"
        "   Пример: `/remind 7200 Пить воду`\n\n"
        "`/search [запрос]` — Ссылки на Google, DuckDuckGo, YouTube, Wikipedia.\n\n"
        "`/shorten [url]` — Сократить ссылку через TinyURL.\n\n"
        "`/weather [город]` — Ссылки на прогноз: wttr.in, OpenWeatherMap, Weather.com.\n\n"
        "`/translate [текст]` — Google Translate: RU→EN, EN→RU, Auto→RU.\n\n"
        "`/base64 encode/decode [текст]` — Base64 кодирование/декодирование.\n\n"
        "`/hash [текст]` — MD5, SHA1, SHA256, SHA512.\n\n"
        "`/morse [текст]` — Перевод в азбуку Морзе.\n\n"
        "`/caesar encode/decode [сдвиг] [текст]` — Шифр Цезаря (RU + EN).\n"
        "   Пример: `/caesar encode 13 Hello`\n\n"
        "`/vigenere encode/decode [ключ] [текст]` — Шифр Виженера.\n"
        "   Пример: `/vigenere encode KEY Secret text`\n\n"
        "`/password [длина] [simple]` — Пароль до 128 симв. `simple` — без спецсимволов.\n\n"
        "`/qr [текст]` — Ссылка на QR-код 400×400.\n\n"
        "`/uuid` — 5 случайных UUID v4.\n\n"
        "`/color [#HEX или R,G,B]` — HEX↔RGB↔HSL + ссылка на превью.\n"
        "   Примеры: `/color #FF5733` или `/color 255,87,51`\n\n"
        "`/ascii [текст]` — ASCII коды символов и обратное преобразование."
    ),
    'сообщения': (
        "✉️ **УПРАВЛЕНИЕ СООБЩЕНИЯМИ (14 команд)**\n\n"
        "`/type [режим] [текст]` — Эффект печати. Режимы:\n"
        "   • (без режима) — стандартная печать с курсором ▌\n"
        "   • `fast` — быстрая (по 2 символа)\n"
        "   • `slow` — медленная с паузами на знаках препинания\n"
        "   • `matrix` — Матрица: символы «падают» и проявляется текст\n"
        "   • `glitch` — глич-эффект: текст мигает перед появлением\n"
        "   Пример: `/type matrix Привет мир`\n\n"
        "`/echo [текст]` — Удалить команду и отправить чистый текст.\n\n"
        "`/say [текст]` — Отправить текст без следа команды.\n\n"
        "`/bold [текст]` — Отправить **жирный** текст.\n\n"
        "`/italic [текст]` — Отправить _курсивный_ текст.\n\n"
        "`/mono [текст]` — Отправить `моноширинный` текст.\n\n"
        "`/clean [n]` — Удалить свои последние N сообщений. По умолчанию 10. Без лимита.\n\n"
        "`/purge [n]` — ⚠️ Удалить ЛЮБЫЕ последние N сообщений. Без ограничений.\n\n"
        "`/spam [n] [текст]` — Отправить текст N раз. Без ограничений.\n"
        "   Пример: `/spam 10 Привет!`\n\n"
        "`/forward [chat_id]` — Переслать сообщение (ответом) в другой чат.\n\n"
        "`/pin` — Закрепить сообщение (ответом) без уведомления.\n\n"
        "`/unpin` — Открепить сообщение (ответом) или последнее закреплённое.\n\n"
        "`/copyall [n] [chat_id]` — Скопировать N сообщений в другой чат.\n"
        "   Пример: `/copyall 50 -1001234567890`\n\n"
        "`/react [эмодзи]` — Поставить реакцию на сообщение (ответом).\n"
        "   Пример: `/react 👍`"
    ),
    'заметки': (
        "📦 **ЗАМЕТКИ И TODO (12 команд)**\n\n"
        "**— Быстрое хранилище:**\n\n"
        "`/save [ключ] [значение]` — Сохранить текст под ключом.\n\n"
        "`/get [ключ]` — Получить значение по ключу.\n\n"
        "`/del [ключ]` — Удалить запись.\n\n"
        "`/list` — Список всех сохранённых записей.\n\n"
        "**— Заметки:**\n\n"
        "`/note [название] [текст]` — Сохранить заметку. Ответом — возьмёт текст сообщения.\n\n"
        "`/getnote [название]` — Показать заметку полностью.\n\n"
        "`/delnote [название]` — Удалить заметку.\n\n"
        "`/notes` — Список всех заметок с превью.\n\n"
        "**— Список задач (TODO):**\n\n"
        "`/todo [задача]` — Добавить задачу в список.\n\n"
        "`/todos` — Показать все задачи с отметками.\n\n"
        "`/done [номер]` — Отметить задачу выполненной.\n\n"
        "`/undone [номер]` — Снять отметку выполнения.\n\n"
        "`/deltodo [номер]` — Удалить задачу из списка."
    ),
    'afk': (
        "😴 **AFK — РЕЖИМ ОТСУТСТВИЯ (2 команды)**\n\n"
        "`/afk` — Включить AFK. Все кто напишут в личку получат уведомление с временем отсутствия.\n\n"
        "`/afk [причина]` — AFK с причиной.\n"
        "   Пример: `/afk сплю до утра`\n\n"
        "`/unafk` — Выключить AFK. Покажет сколько времени отсутствовал.\n\n"
        "ℹ️ Каждый пользователь уведомляется не чаще раза в 60 секунд.\n"
        "ℹ️ AFK и автоответчик работают независимо."
    ),
    'инфо': (
        "📊 **ИНФОРМАЦИЯ О ЧАТЕ (5 команд)**\n\n"
        "`/chatinfo` — Название, ID, username, тип чата, кол-во участников.\n\n"
        "`/members` — Подсчёт участников: всего, люди и боты.\n\n"
        "`/admins` — Список всех администраторов с username.\n\n"
        "`/top [n]` — Топ-10 активных по сообщениям из последних N (по умол. 200).\n"
        "   Пример: `/top 1000`\n\n"
        "`/bots` — Список всех ботов в чате."
    ),
}

@client.on(events.NewMessage(pattern=r'/help(?:\s+(.+))?$', func=is_me))
async def help_cmd(e):
    cat = (e.pattern_match.group(1) or '').strip().lower()
    if cat and cat in HELP_CATS:
        await e.reply(HELP_CATS[cat]); return
    if cat:
        await e.reply(f"❌ Категория `{cat}` не найдена.\nДоступные: `{', '.join(HELP_CATS)}`"); return

    await e.reply(
        "📚 **UserBot — 70 команд**\n\n"
        "Пиши `/help [категория]` для подробного описания:\n\n"
        "⚙️ `/help основные` — 9 команд\n"
        "   sleep, wake, setreply, status, time, ping, id, info, restart, ghost\n\n"
        "👤 `/help профиль` — 7 команд\n"
        "   me, avatar, name, bio, lastname, whois, username_check\n\n"
        "🎮 `/help игры` — 12 команд\n"
        "   dice, dart, basket, football, bowling, casino, coin, rand, 8ball, rps, slot, lucky, choose, quiz\n\n"
        "🛠 `/help утилиты` — 14 команд\n"
        "   calc, remind, search, shorten, weather, translate, base64, hash, morse, caesar, vigenere, password, qr, uuid, color, ascii\n\n"
        "✉️ `/help сообщения` — 14 команд\n"
        "   type (fast/slow/matrix/glitch), echo, say, bold, italic, mono, clean, purge, spam, forward, pin, unpin, copyall, react\n\n"
        "📦 `/help заметки` — 12 команд\n"
        "   save, get, del, list, note, getnote, delnote, notes, todo, todos, done, undone, deltodo\n\n"
        "😴 `/help afk` — 2 команды\n"
        "   afk, unafk\n\n"
        "📊 `/help инфо` — 5 команд\n"
        "   chatinfo, members, admins, top, bots\n\n"
        "⚠️ `/purge` и `/spam` работают без каких-либо ограничений!"
    )
    bump_stat('cmds')

# ══════════════════════════════════════════════════
#  ВХОДЯЩИЕ СООБЩЕНИЯ (автоответчик + AFK)
# ══════════════════════════════════════════════════

@client.on(events.NewMessage(incoming=True))
async def incoming_handler(event):
    # Пропускаем свои сообщения
    if event.sender_id == MY_ID:
        return
    
    # Только личные сообщения
    if not event.is_private:
        return
    
    sender = await event.get_sender()
    if not sender or sender.bot:
        return
    
    uid = event.sender_id
    now = time.time()

    if afk_start_time and now - reply_cooldown.get(f'afk_{uid}', 0) > 60:
        dur = fmt_time(now - afk_start_time)
        r   = f"\n📝 _{afk_reason}_" if afk_reason else ""
        reply_cooldown[f'afk_{uid}'] = now
        await event.reply(f"😴 Хозяин AFK уже **{dur}**{r}")

    if auto_reply_enabled and now - reply_cooldown.get(uid, 0) > 10:
        reply_cooldown[uid] = now
        await asyncio.sleep(1)
        await event.reply(auto_reply_text)

# ══════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════

if __name__ == "__main__":
    print("🚀 Запуск UserBot (70 команд)...")
    Thread(target=run_web, daemon=True).start()
    client.start()
    
    # Устанавливаем MY_ID после запуска
    loop = asyncio.get_event_loop()
    MY_ID = loop.run_until_complete(client.get_me()).id
    print(f"✅ UserBot запущен! ID: {MY_ID}")
    
    client.run_until_disconnected()
