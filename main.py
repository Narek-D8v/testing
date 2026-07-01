import os
import logging
import datetime
import asyncio
import random
import json
import ast
import re
import math
import time
import hashlib
import base64
import string
import uuid
import aiohttp
from collections import defaultdict
from telethon import TelegramClient, events
from telethon.tl.types import (
    InputMediaDice,
    ChannelParticipantsAdmins, ChannelParticipantsBots,
    ReactionEmoji
)
from telethon.tl.functions.messages import SendReactionRequest
from flask import Flask
from threading import Thread
import sys

import rp_commands   # должен существовать

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ─── Конфигурация ──────────────────────────────────────────
API_ID  = os.environ.get('API_ID')
API_HASH = os.environ.get('API_HASH')
PORT = int(os.environ.get('PORT', 8080))
STRING_SESSION = os.environ.get('STRING_SESSION')

if not API_ID or not API_HASH:
    raise SystemExit("❌ API_ID и API_HASH должны быть заданы в переменных окружения!")

DATA_FILE  = 'userbot_data.json'
SAVED_FILE = 'saved_data.json'
NOTES_FILE = 'notes_data.json'
TODOS_FILE = 'todos_data.json'
STATS_FILE = 'stats_data.json'

# ─── Класс состояния ──────────────────────────────────────
class BotState:
    def __init__(self):
        self.auto_reply_enabled = False
        self.auto_reply_text = '💫 Я автоответчик, хозяин скоро ответит! Спасибо за терпение 😘'
        self.ghost_mode = False
        self.afk_start_time = None
        self.afk_reason = ''
        self.bot_start_time = time.time()
        # Stealth modes
        self.cover_enabled = False
        self.silent_enabled = False
        self.shadow_enabled = False
        self.lock_enabled = False
        self.mute_enabled = False
        self.hide_enabled = False
        self._load()

    def _load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                    self.auto_reply_enabled = d.get('auto_reply_enabled', False)
                    self.auto_reply_text    = d.get('auto_reply_text', self.auto_reply_text)
                    self.ghost_mode         = d.get('ghost_mode', False)
                    self.afk_start_time     = d.get('afk_start_time', None)
                    self.afk_reason         = d.get('afk_reason', '')
                    self.cover_enabled      = d.get('cover_enabled', False)
                    self.silent_enabled     = d.get('silent_enabled', False)
                    self.shadow_enabled     = d.get('shadow_enabled', False)
                    self.lock_enabled       = d.get('lock_enabled', False)
                    self.mute_enabled       = d.get('mute_enabled', False)
                    self.hide_enabled       = d.get('hide_enabled', False)
            except Exception as e:
                logger.error(f"Ошибка загрузки состояния: {e}")

    def save(self):
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'auto_reply_enabled': self.auto_reply_enabled,
                    'auto_reply_text': self.auto_reply_text,
                    'ghost_mode': self.ghost_mode,
                    'afk_start_time': self.afk_start_time,
                    'afk_reason': self.afk_reason,
                    'cover_enabled': self.cover_enabled,
                    'silent_enabled': self.silent_enabled,
                    'shadow_enabled': self.shadow_enabled,
                    'lock_enabled': self.lock_enabled,
                    'mute_enabled': self.mute_enabled,
                    'hide_enabled': self.hide_enabled,
                }, f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка сохранения состояния: {e}")

    def toggle_auto_reply(self, state=None):
        if state is None:
            self.auto_reply_enabled = not self.auto_reply_enabled
        else:
            self.auto_reply_enabled = state
        self.save()

    def set_auto_reply_text(self, text):
        self.auto_reply_text = text
        self.save()

    def toggle_ghost(self, state=None):
        if state is None:
            self.ghost_mode = not self.ghost_mode
        else:
            self.ghost_mode = state
        self.save()

    def set_cover(self, state=None):
        if state is None:
            self.cover_enabled = not self.cover_enabled
        else:
            self.cover_enabled = state
        self.save()

    def set_silent(self, state=None):
        if state is None:
            self.silent_enabled = not self.silent_enabled
        else:
            self.silent_enabled = state
        self.save()

    def set_shadow(self, state=None):
        if state is None:
            self.shadow_enabled = not self.shadow_enabled
        else:
            self.shadow_enabled = state
        self.save()

    def set_lock(self, state=None):
        if state is None:
            self.lock_enabled = not self.lock_enabled
        else:
            self.lock_enabled = state
        self.save()

    def set_mute(self, state=None):
        if state is None:
            self.mute_enabled = not self.mute_enabled
        else:
            self.mute_enabled = state
        self.save()

    def set_hide(self, state=None):
        if state is None:
            self.hide_enabled = not self.hide_enabled
        else:
            self.hide_enabled = state
        self.save()

    def set_afk(self, reason=''):
        self.afk_start_time = time.time()
        self.afk_reason = reason
        self.save()

    def clear_afk(self):
        duration = None
        if self.afk_start_time:
            duration = time.time() - self.afk_start_time
        self.afk_start_time = None
        self.afk_reason = ''
        self.save()
        return duration

    @property
    def uptime(self):
        return fmt_time(time.time() - self.bot_start_time)

# ─── Вспомогательные функции ──────────────────────────────
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

json_lock = asyncio.Lock()

async def load_json(path, default=None):
    async with json_lock:
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return default
        return default

async def write_json(path, data):
    async with json_lock:
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка записи {path}: {e}")

async def bump_stat(key, n=1):
    d = await load_json(STATS_FILE, {})
    d[key] = d.get(key, 0) + n
    await write_json(STATS_FILE, d)

# ─── Глобальные переменные ──────────────────────────────────
state = BotState()
reply_cooldown = defaultdict(float)

# ─── Клиент Telegram ──────────────────────────────────────
def create_client():
    if STRING_SESSION:
        from telethon.sessions import StringSession
        session = StringSession(STRING_SESSION)
        return TelegramClient(session, int(API_ID), API_HASH)
    else:
        return TelegramClient('my_userbot', int(API_ID), API_HASH)

client = create_client()

# ─── Flask ─────────────────────────────────────────────────
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 UserBot работает 24/7!"

def run_web():
    app.run(host='0.0.0.0', port=PORT)   # исправлено для Render

# ════════════════════════════════════════════════════════════
# 1. ВСЕ ОСНОВНЫЕ КОМАНДЫ (префикс !)
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'!sleep$', from_users='me'))
async def sleep_cmd(e):
    state.toggle_auto_reply(True)
    await e.edit(f"💤 **Автоответчик**\n\nСтатус: ✅ **Включён**\nТекст: _{state.auto_reply_text}_")

@client.on(events.NewMessage(pattern=r'!wake$', from_users='me'))
async def wake_cmd(e):
    state.toggle_auto_reply(False)
    await e.edit('☀️ **Автоответчик**\n\nСтатус: ❌ **Выключен**')

@client.on(events.NewMessage(pattern=r'!setreply (.+)', from_users='me'))
async def setreply_cmd(e):
    text = e.pattern_match.group(1).strip()
    state.set_auto_reply_text(text)
    await e.edit(f"✍️ **Текст автоответчика обновлён**\n\n_{text}_")

@client.on(events.NewMessage(pattern=r'!status$', from_users='me'))
async def status_cmd(e):
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    s = await load_json(STATS_FILE, {})
    reply_text = state.auto_reply_text if state.auto_reply_enabled else "—"
    afk_status = f"{state.afk_reason or 'без причины'}" if state.afk_start_time else "—"
    lines = [
        f"📊 **Статус UserBot**\n",
        f"👤 **Аккаунт:** {me.first_name} {me.last_name or ''} (`{me.id}`)",
        f"💬 **Чатов:** `{len(dialogs)}`",
        f"⏱ **Аптайм:** `{state.uptime}`",
        f"",
        f"🔹 **Автоответчик:** {'✅ Вкл' if state.auto_reply_enabled else '❌ Выкл'}",
        f"🔹 **Ghost:** {'✅ Вкл' if state.ghost_mode else '❌ Выкл'}",
        f"🔹 **AFK:** {'✅ ' + afk_status if state.afk_start_time else '❌'}",
        f"",
        f"📨 **Команд выполнено:** `{s.get('cmds', 0)}`",
    ]
    if state.auto_reply_enabled:
        lines.append(f"💬 **Текст:** _{reply_text}_")
    await e.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'!time$', from_users='me'))
async def time_cmd(e):
    now = datetime.datetime.now()
    utc = datetime.datetime.utcnow()
    week_days = ['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье']
    months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
    day_of_year = now.timetuple().tm_yday
    week_num = now.isocalendar()[1]
    offset_h = int((now - utc).total_seconds() // 3600)
    tz = f"UTC{offset_h:+d}" if offset_h else "UTC"
    await e.edit(
        f"🕐 **Время и дата**\n\n"
        f"📅 `{now.day}` {months[now.month-1]} `{now.year}`"
        f" — {week_days[now.weekday()]}\n"
        f"───\n"
        f"🏠 **Локальное ({tz}):** `{now.strftime('%H:%M:%S')}`\n"
        f"🌍 **UTC:** `{utc.strftime('%H:%M:%S')}`\n"
        f"───\n"
        f"📊 **День года:** `{day_of_year}/365`\n"
        f"📋 **Неделя:** `#{week_num}`"
    )

@client.on(events.NewMessage(pattern=r'!ping$', from_users='me'))
async def ping_cmd(e):
    t0 = time.monotonic()
    msg = await e.edit("🏓 Pinging...")
    ms = (time.monotonic() - t0) * 1000
    emoji = "🟢" if ms < 150 else "🟡" if ms < 400 else "🔴"
    label = "Отлично" if ms < 150 else "Нормально" if ms < 400 else "Высокая"
    bar = progress_bar(int(ms), 500, 10)
    await msg.edit(
        f"🏓 **Pong!**\n\n"
        f"{emoji} `{ms:.0f} мс`\n"
        f"{bar} **{label}**"
    )

@client.on(events.NewMessage(pattern=r'!id$', from_users='me'))
async def id_cmd(e):
    chat = await e.get_chat()
    chat_name = getattr(chat, 'title', None) or f"{getattr(chat, 'first_name', '')} {getattr(chat, 'last_name', '')}".strip()
    lines = [
        f"🆔 **ID**\n",
        f"💬 **Чат:** {chat_name or '—'}",
        f"🆔 `{chat.id}`",
    ]
    if e.reply_to_msg_id:
        r = await e.get_reply_message()
        lines += [
            f"",
            f"📨 **Сообщение:** `{r.id}`",
            f"👤 **Отправитель:** `{r.sender_id}`",
        ]
        if r.sender and getattr(r.sender, 'username', None):
            lines.append(f"🔖 **Username:** @{r.sender.username}")
    else:
        me = await client.get_me()
        lines += [
            f"",
            f"👤 **Мой ID:** `{me.id}`",
            f"🔖 **Username:** @{me.username or '—'}",
        ]
    await e.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'!info$', from_users='me'))
async def info_cmd(e):
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    photos = await client.get_profile_photos(me.id, limit=1)
    days = int(time.time() - state.bot_start_time) // 86400
    await e.edit(
        f"🚀 **Информация о боте**\n\n"
        f"👤 **{me.first_name} {me.last_name or ''}**\n"
        f"🆔 `{me.id}`\n"
        f"🔗 @{me.username or '—'}\n"
        f"📱 `{me.phone or '—'}`\n"
        f"───\n"
        f"💬 **Чатов:** `{len(dialogs)}`\n"
        f"🖼 **Аватар:** {'✅' if photos else '❌'}\n"
        f"✅ **Verified:** {'✅' if me.verified else '❌'}\n"
        f"───\n"
        f"⏱ **Аптайм:** `{state.uptime}`{' 🗓' if days else ''}\n"
        f"⚡ **Статус:** Активен ✅"
    )

@client.on(events.NewMessage(pattern=r'!restart$', from_users='me'))
async def restart_cmd(e):
    await e.edit('🔄 **Перезагрузка...**\n\nСохраняю состояние...')
    state.save()
    await asyncio.sleep(1)
    await e.edit('🔄 **Перезагрузка...**\n\nОтключаюсь...')
    await client.disconnect()
    sys.exit(0)

@client.on(events.NewMessage(pattern=r'!ghost$', from_users='me'))
async def ghost_cmd(e):
    state.toggle_ghost()
    if state.ghost_mode:
        await e.edit("👻 **Ghost-режим ВКЛЮЧЁН** — команды удаляются мгновенно")
        await asyncio.sleep(2)
        await e.delete()
    else:
        await e.edit("👁 **Ghost-режим ВЫКЛЮЧЕН**")

# ─── ПРОФИЛЬ ──────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r'!me$', from_users='me'))
async def me_cmd(e):
    me = await client.get_me()
    photos = await client.get_profile_photos(me.id, limit=1)
    lang = getattr(me, 'lang_code', '—')
    await e.edit(
        f"👤 **Мой профиль**\n\n"
        f"📛 **{me.first_name} {me.last_name or ''}**\n"
        f"🆔 `{me.id}`\n"
        f"🔗 @{me.username or '—'}\n"
        f"📱 `{me.phone or '—'}`\n"
        f"───\n"
        f"🖼 **Аватар:** {'✅' if photos else '❌'}\n"
        f"🔰 **Verified:** {'✅' if me.verified else '❌'}\n"
        f"🤖 **Бот:** {'✅' if me.bot else '❌'}\n"
        f"🌐 **Язык:** `{lang}`"
    )

@client.on(events.NewMessage(pattern=r'!avatar$', from_users='me'))
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
        await e.edit("❌ Аватарка не найдена")

@client.on(events.NewMessage(pattern=r'!name (.+)', from_users='me'))
async def name_cmd(e):
    n = e.pattern_match.group(1).strip()
    await client.edit_profile(first_name=n)
    await e.edit(f"✅ Имя → **{n}**")

@client.on(events.NewMessage(pattern=r'!lastname(?:\s+(.+))?$', from_users='me'))
async def lastname_cmd(e):
    n = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(last_name=n)
    await e.edit(f"✅ Фамилия → **{n}**" if n else "✅ Фамилия удалена")

@client.on(events.NewMessage(pattern=r'!bio(?:\s+(.+))?$', from_users='me'))
async def bio_cmd(e):
    t = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(about=t)
    await e.edit(f"✅ Био → _{t}_" if t else "✅ Био очищено")

@client.on(events.NewMessage(pattern=r'!whois (.+)', from_users='me'))
async def whois_cmd(e):
    target = e.pattern_match.group(1).strip().lstrip('@')
    try:
        ent = await client.get_entity(target)
        name = f"{getattr(ent, 'first_name', '') or ''} {getattr(ent, 'last_name', '') or ''}".strip() \
               or getattr(ent, 'title', '?')
        uname = f"@{ent.username}" if getattr(ent, 'username', None) else "—"
        is_bot = "✅ Бот" if getattr(ent, 'bot', False) else "❌ Человек"
        verified = "✅" if getattr(ent, 'verified', False) else "❌"
        scam = "⚠️ Да" if getattr(ent, 'scam', False) else "✅ Нет"
        fake = "⚠️ Да" if getattr(ent, 'fake', False) else "✅ Нет"
        photos = await client.get_profile_photos(ent.id, limit=1)
        await e.edit(
            f"🔍 **Информация о пользователе**\n\n"
            f"📛 **{name}**\n"
            f"🆔 `{ent.id}`\n"
            f"🔗 {uname}\n"
            f"───\n"
            f"🤖 {is_bot}\n"
            f"✔️ **Verified:** {verified}\n"
            f"🖼 **Аватар:** {'✅' if photos else '❌'}\n"
            f"⚠️ **Scam:** {scam}\n"
            f"🎭 **Fake:** {fake}"
        )
    except Exception as ex:
        await e.edit(f"❌ **Пользователь не найден**\n\n`{ex}`")

@client.on(events.NewMessage(pattern=r'!username_check (.+)', from_users='me'))
async def username_check_cmd(e):
    uname = e.pattern_match.group(1).strip().lstrip('@')
    try:
        ent = await client.get_entity(uname)
        name = getattr(ent, 'first_name', None) or getattr(ent, 'title', '?')
        await e.edit(
            f"🔍 **Проверка username**\n\n"
            f"@{uname}\n"
            f"───\n"
            f"📛 **Занят**\n"
            f"👤 {name}\n"
            f"🆔 `{ent.id}`"
        )
    except Exception:
        await e.edit(
            f"🔍 **Проверка username**\n\n"
            f"@{uname}\n"
            f"───\n"
            f"✅ **Свободен**"
        )

# ─── ИГРЫ ──────────────────────────────────────────────────
DICE_EMOJI = {'dice': '🎲', 'dart': '🎯', 'basket': '🏀', 'football': '⚽', 'bowling': '🎳', 'casino': '🎰'}

@client.on(events.NewMessage(pattern=r'!(dice|dart|basket|football|bowling|casino)$', from_users='me'))
async def dice_cmd(e):
    game = e.pattern_match.group(1)
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice(DICE_EMOJI[game]))
    await bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!coin$', from_users='me'))
async def coin_cmd(e):
    sides = [("Орёл", "🦅"), ("Решка", "💰")]
    flips = random.randint(3, 9)
    name, emoji = random.choice(sides)
    msg = await e.edit("🪙 Подбрасываю монету...")
    for _ in range(flips):
        n, e2 = random.choice(sides)
        await msg.edit(f"🪙 {e2} {n}...")
        await asyncio.sleep(0.15)
    await msg.edit(f"🪙 **Монетка**\n\n───\n{emoji} **{name}**\n───\n\n_Выпал(а) после {flips} вращений_")
    await bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!rand(?:\s+(-?\d+)(?:\s+(-?\d+))?)?$', from_users='me'))
async def rand_cmd(e):
    g = e.pattern_match
    a, b = g.group(1), g.group(2)
    if a and b:
        lo, hi = sorted([int(a), int(b)])
        result = random.randint(lo, hi)
        await e.edit(f"🎲 **Случайное число**\n\nДиапазон: `{lo}` – `{hi}`\n───\n**Результат:** `{result}`")
    elif a:
        result = random.randint(1, int(a))
        await e.edit(f"🎲 **Случайное число**\n\nДиапазон: `1` – `{a}`\n───\n**Результат:** `{result}`")
    else:
        result = random.randint(1, 100)
        await e.edit(f"🎲 **Случайное число**\n\nДиапазон: `1` – `100`\n───\n**Результат:** `{result}`")
    await bump_stat('cmds')

EIGHTBALL_ANSWERS = {
    'pos': [
        ("Определённо да", "✅", "Вселенная согласна с тобой."),
        ("Без сомнений", "💯", "Это решено раньше, чем ты спросил."),
        ("Скорее всего да", "👍", "Всё складывается в твою пользу."),
        ("Хорошие перспективы", "🌟", "Будущее выглядит светлым."),
        ("Знаки говорят «да»", "🔮", "Мистические силы на твоей стороне."),
        ("Да, и поскорее", "🚀", "Не медли — действуй прямо сейчас."),
        ("Это неизбежно", "⚡", "Ничто не остановит это."),
        ("Вселенная шепчет: да", "🌌", "Даже звёзды кивают."),
    ],
    'neu': [
        ("Пока не ясно", "🤔", "Туман будущего слишком густой."),
        ("Спроси позже", "⏰", "Момент ещё не настал."),
        ("Не могу предсказать", "🌫", "Слишком много переменных."),
        ("Сосредоточься и повтори", "🧘", "Твой разум мешает ответу."),
        ("Лучше не рассказывать", "🤫", "Некоторые тайны лучше хранить."),
        ("Ответ где-то рядом", "🔭", "Смотри внимательнее вокруг себя."),
    ],
    'neg': [
        ("Мой ответ — нет", "🚫", "Прими это спокойно."),
        ("Перспективы не очень", "😕", "Стоит пересмотреть планы."),
        ("Весьма сомнительно", "🙄", "Интуиция говорит «осторожно»."),
        ("Точно нет", "💀", "Даже не думай об этом."),
        ("Категорически нет", "🔴", "Вселенная против."),
        ("Шансы ничтожны", "🎰", "Даже удача отвернулась."),
    ],
}

@client.on(events.NewMessage(pattern=r'!8ball(?:\s+(.+))?$', from_users='me'))
async def eightball_cmd(e):
    question = (e.pattern_match.group(1) or '').strip()
    spin = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
    msg = await e.edit("🎱 Шар вращается...")
    for frame in spin:
        await msg.edit(f"{frame} Шар вращается...")
        await asyncio.sleep(0.12)

    pool_key = random.choices(['pos', 'neu', 'neg'], weights=[38, 27, 35])[0]
    answer, emoji_text, comment = random.choice(EIGHTBALL_ANSWERS[pool_key])
    color = {"pos": "🟢", "neu": "🟡", "neg": "🔴"}[pool_key]
    label = {"pos": "ПОЗИТИВНЫЙ", "neu": "НЕЙТРАЛЬНЫЙ", "neg": "НЕГАТИВНЫЙ"}[pool_key]
    confidence = random.randint(55, 99)
    bar = progress_bar(confidence, 100, 10)

    lines = [f"🎱 **Магический шар**"]
    if question:
        lines.append(f"\n❓ _{question}_")
    lines.append(f"\n{'─'*22}")
    lines.append(f"{emoji_text}  **{answer}**")
    lines.append(f"{'─'*22}")
    lines.append(f"\n💬 _{comment}_")
    lines.append(f"\n{color} {label}")
    lines.append(f"[{bar}] **{confidence}%** уверенности")

    await msg.edit("\n".join(lines))
    await bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!rps(?:\s+(.+))?$', from_users='me'))
async def rps_cmd(e):
    MAP = {'к':'🪨 Камень','камень':'🪨 Камень','н':'✂️ Ножницы','ножницы':'✂️ Ножницы','б':'📄 Бумага','бумага':'📄 Бумага'}
    BOT = ['🪨 Камень','✂️ Ножницы','📄 Бумага']
    WIN = {'🪨 Камень':'✂️ Ножницы','✂️ Ножницы':'📄 Бумага','📄 Бумага':'🪨 Камень'}
    arg = (e.pattern_match.group(1) or '').lower().strip()
    if not arg or arg not in MAP:
        await e.edit("✊✌️🖐 **Камень–Ножницы–Бумага**\n\nИспользуй: `!rps камень` / `ножницы` / `бумага`\nИли сокращённо: `к` / `н` / `б`")
        return
    uc, bc = MAP[arg], random.choice(BOT)
    if uc == bc:
        res = "🤝 **Ничья!**"
        res_emoji = "🤝"
    elif WIN[uc] == bc:
        res = "🏆 **Ты победил!**"
        res_emoji = "🏆"
    else:
        res = "💀 **Бот победил!**"
        res_emoji = "💀"
    await e.edit(
        f"✊✌️🖐 **Камень–Ножницы–Бумага**\n\n"
        f"👤 **Ты:** {uc}\n"
        f"🤖 **Бот:** {bc}\n"
        f"───\n"
        f"{res}"
    )
    await bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!slot$', from_users='me'))
async def slot_cmd(e):
    SYM = ['🍒','🍋','🍊','🍇','🍉','⭐','💎','7️⃣','🔔','🍀']
    msg = await e.edit("🎰 [ ▓ | ▓ | ▓ ]")
    await asyncio.sleep(0.3)
    for _ in range(4):
        s = [random.choice(SYM) for _ in range(3)]
        await msg.edit(f"🎰 [ {s[0]} | {s[1]} | {s[2]} ]")
        await asyncio.sleep(0.25)
    s = [random.choice(SYM) for _ in range(3)]
    if s[0] == s[1] == s[2]:
        res = "💰💰💰 **ДЖЕКПОТ!**" if s[0] in ('💎','7️⃣') else "🎊 **Три одинаковых!**"
        res_emoji = "💰" if s[0] in ('💎','7️⃣') else "🎊"
    elif len(set(s)) < 3:
        res = "😅 Почти! Два совпали"
        res_emoji = "😅"
    else:
        res = "💸 Всё разные"
        res_emoji = "💸"
    await msg.edit(
        f"🎰 **Слот-машина**\n\n"
        f"[ {s[0]} | {s[1]} | {s[2]} ]\n"
        f"───\n"
        f"{res_emoji} {res}"
    )
    await bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!lucky$', from_users='me'))
async def lucky_cmd(e):
    pct = random.randint(0, 100)
    bar = progress_bar(pct, 100, 12)
    emoji = "🌟" if pct >= 90 else "🍀" if pct >= 70 else "😊" if pct >= 50 else "😐" if pct >= 30 else "😬" if pct >= 10 else "💀"
    tips = {
        (90, 100): "АБСОЛЮТНАЯ УДАЧА! Сегодня твой день!",
        (70, 89): "Очень удачный день — действуй!",
        (50, 69): "Неплохо — удача на твоей стороне",
        (30, 49): "Средний день, будь осторожен",
        (10, 29): "Не лучший день...",
        (0, 9): "Сиди дома и не высовывайся!",
    }
    tip = next(v for (a, b), v in tips.items() if a <= pct <= b)
    await e.edit(
        f"🔮 **Индекс удачи**\n\n"
        f"[{bar}] **{pct}%**\n\n"
        f"{emoji} _{tip}_"
    )
    await bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!choose (.+)', from_users='me'))
async def choose_cmd(e):
    raw = e.pattern_match.group(1)
    opts = [o.strip() for o in re.split(r'[,|/]', raw) if o.strip()]
    if len(opts) < 2:
        await e.edit("ℹ️ **Choose**\n\nПеречисли варианты через запятую:\n`!choose пицца, суши, бургер`")
        return
    winner = random.choice(opts)
    listed = "\n".join(f"{'  •'} {o}" for o in opts)
    await e.edit(
        f"🤔 **Выбор из {len(opts)}**\n\n"
        f"{listed}\n"
        f"───\n"
        f"✅ **Выбрано:** `{winner}`"
    )

@client.on(events.NewMessage(pattern=r'!quiz$', from_users='me'))
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
    n = random.randint(0, len(QUESTIONS) - 1)
    q, opts, ans_idx = QUESTIONS[n]
    letters = ['A','B','C','D']
    opts_text = "\n".join(f"{'  '}{letters[i]}. {o}" for i,o in enumerate(opts))
    correct = f"{letters[ans_idx]}. {opts[ans_idx]}"
    percents = [random.randint(5, 40) for _ in range(4)]
    percents[ans_idx] = max(percents[ans_idx], 60)
    total = sum(percents)
    percents = [round(p / total * 100) for p in percents]
    stats = "  ".join(f"{letters[i]} {percents[i]}%" for i in range(4))
    await e.edit(
        f"🧠 **Викторина**\n\n"
        f"Вопрос #{n+1}:\n"
        f"_{q}_\n\n"
        f"{opts_text}\n\n"
        f"───\n"
        f"📊 {stats}\n"
        f"───\n"
        f"||✅ **{correct}**||"
    )

# ─── УТИЛИТЫ ──────────────────────────────────────────────
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
    ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
               ast.FloorDiv, ast.USub, ast.UAdd, ast.Call, ast.Name, ast.Attribute)
    try:
        tree = ast.parse(safe, mode='eval')
        for node in ast.walk(tree):
            if not isinstance(node, ALLOWED):
                return None
            if isinstance(node, ast.Call) and not isinstance(node.func, (ast.Name, ast.Attribute)):
                return None
        ns = {'__builtins__': {}, 'math': math}
        r = eval(compile(tree, '<safe>', 'eval'), ns, {})
        if isinstance(r, float):
            if math.isinf(r) or math.isnan(r):
                return "∞"
            return round(r, 10)
        return r
    except Exception:
        return None

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
    if not key:
        return text
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

@client.on(events.NewMessage(pattern=r'!calc (.+)', from_users='me'))
async def calc_cmd(e):
    expr = e.pattern_match.group(1).strip()
    r = await safe_eval(expr)
    if r is not None:
        await e.edit(f"🧮 **Калькулятор**\n\n`{expr}`\n───\n**=** `{r}`")
    else:
        await e.edit(
            f"❌ **Ошибка вычисления**\n\n"
            f"Выражение: `{expr}`\n\n"
            f"Доступно: `+ - * / % sqrt sin cos tan log abs pow pi e factorial ceil floor round`"
        )

async def send_reminder(chat_id, msg, delay):
    await asyncio.sleep(delay)
    try:
        await client.send_message(chat_id, f"⏰ **НАПОМИНАНИЕ:**\n{msg}")
    except Exception as e:
        logger.error(f"Ошибка напоминания: {e}")

@client.on(events.NewMessage(pattern=r'!remind (\d+) (.+)', from_users='me'))
async def remind_cmd(e):
    delay = int(e.pattern_match.group(1))
    text = e.pattern_match.group(2).strip()
    eta = time.time() + delay
    eta_str = datetime.datetime.fromtimestamp(eta).strftime('%H:%M:%S')
    await e.edit(
        f"⏰ **Напоминание**\n\n"
        f"Через: `{fmt_time(delay)}`\n"
        f"Время: `{eta_str}`\n"
        f"───\n"
        f"📝 _{text}_\n\n"
        f"_⏳ Уведомление придёт в чат_"
    )
    asyncio.create_task(send_reminder(e.chat_id, text, delay))

@client.on(events.NewMessage(pattern=r'!search (.+)', from_users='me'))
async def search_cmd(e):
    q = e.pattern_match.group(1).strip()
    enc = q.replace(' ','+')
    await e.edit(
        f"🔍 **Поиск**\n\n"
        f"Запрос: _{q}_\n\n"
        f"🔗 [Google](https://www.google.com/search?q={enc})\n"
        f"🔗 [DuckDuckGo](https://duckduckgo.com/?q={enc})\n"
        f"🔗 [YouTube](https://www.youtube.com/results?search_query={enc})\n"
        f"🔗 [Wikipedia](https://ru.wikipedia.org/wiki/Special:Search?search={enc})"
    )

@client.on(events.NewMessage(pattern=r'!shorten (.+)', from_users='me'))
async def shorten_cmd(e):
    url = e.pattern_match.group(1).strip()
    await e.edit("⏳ **Сокращаю ссылку...**")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://tinyurl.com/api-create.php?url={url}",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                short = await r.text()
        if short.startswith('http'):
            await e.edit(
                f"✂️ **Сокращение ссылки**\n\n"
                f"📎 **Оригинал:**\n`{url[:80]}{'…' if len(url)>80 else ''}`\n\n"
                f"🔗 **Короткая:**\n{short.strip()}"
            )
        else:
            raise Exception()
    except Exception:
        await e.edit("❌ **Ошибка**\n\nНе удалось сократить ссылку. Проверь URL.")

@client.on(events.NewMessage(pattern=r'!weather (.+)', from_users='me'))
async def weather_cmd(e):
    city = e.pattern_match.group(1).strip()
    enc = city.replace(' ','+')
    await e.edit(
        f"🌤️ **Погода**\n\n"
        f"Город: _{city}_\n\n"
        f"🔗 [wttr.in](https://wttr.in/{enc})\n"
        f"🔗 [OpenWeatherMap](https://openweathermap.org/find?q={enc})\n"
        f"🔗 [Weather.com](https://weather.com/weather/today/l/{enc})"
    )

@client.on(events.NewMessage(pattern=r'!translate (.+)', from_users='me'))
async def translate_cmd(e):
    text = e.pattern_match.group(1).strip()
    enc = text.replace(' ','%20')
    await e.edit(
        f"🌐 **Перевод**\n\n"
        f"Текст: _{text}_\n\n"
        f"🔗 [RU → EN](https://translate.google.com/?sl=ru&tl=en&text={enc})\n"
        f"🔗 [EN → RU](https://translate.google.com/?sl=en&tl=ru&text={enc})\n"
        f"🔗 [Auto → RU](https://translate.google.com/?sl=auto&tl=ru&text={enc})"
    )

@client.on(events.NewMessage(pattern=r'!base64 (encode|decode) (.+)', from_users='me'))
async def base64_cmd(e):
    mode, text = e.pattern_match.group(1), e.pattern_match.group(2).strip()
    try:
        if mode == 'encode':
            res = base64.b64encode(text.encode()).decode()
            await e.edit(
                f"🔐 **Base64**\n\n"
                f"Режим: `encode`\n"
                f"───\n"
                f"`{res}`"
            )
        else:
            res = base64.b64decode(text.encode()).decode()
            await e.edit(
                f"🔓 **Base64**\n\n"
                f"Режим: `decode`\n"
                f"───\n"
                f"`{res}`"
            )
    except Exception:
        await e.edit("❌ **Ошибка Base64**\n\nПроверь входные данные.")

@client.on(events.NewMessage(pattern=r'!hash (.+)', from_users='me'))
async def hash_cmd(e):
    text = e.pattern_match.group(1).strip().encode()
    await e.edit(
        f"#️⃣ **Хэширование**\n\n"
        f"Текст: `{e.pattern_match.group(1).strip()}`\n"
        f"───\n"
        f"**MD5:**    `{hashlib.md5(text).hexdigest()}`\n"
        f"**SHA1:**   `{hashlib.sha1(text).hexdigest()}`\n"
        f"**SHA256:** `{hashlib.sha256(text).hexdigest()}`\n"
        f"**SHA512:** `{hashlib.sha512(text).hexdigest()[:64]}…`"
    )

@client.on(events.NewMessage(pattern=r'!morse (.+)', from_users='me'))
async def morse_cmd(e):
    text = e.pattern_match.group(1).strip()
    await e.edit(
        f"📡 **Азбука Морзе**\n\n"
        f"Текст: _{text}_\n"
        f"───\n"
        f"`{morse_enc(text)}`"
    )

@client.on(events.NewMessage(pattern=r'!caesar (encode|decode) (\d+) (.+)', from_users='me'))
async def caesar_cmd(e):
    mode, shift, text = e.pattern_match.group(1), int(e.pattern_match.group(2)), e.pattern_match.group(3)
    res = caesar(text, shift, dec=(mode=='decode'))
    label = "Зашифровано" if mode == 'encode' else "Расшифровано"
    await e.edit(
        f"{'🔒' if mode=='encode' else '🔓'} **Шифр Цезаря**\n\n"
        f"Сдвиг: `{shift}`\n"
        f"───\n"
        f"Исходный: _{text}_\n"
        f"───\n"
        f"**{label}:**\n`{res}`"
    )

@client.on(events.NewMessage(pattern=r'!vigenere (encode|decode) (\S+) (.+)', from_users='me'))
async def vigenere_cmd(e):
    mode, key, text = e.pattern_match.group(1), e.pattern_match.group(2), e.pattern_match.group(3)
    res = vigenere(text, key, dec=(mode=='decode'))
    label = "Зашифровано" if mode == 'encode' else "Расшифровано"
    await e.edit(
        f"{'🔒' if mode=='encode' else '🔓'} **Шифр Виженера**\n\n"
        f"Ключ: `{key}`\n"
        f"───\n"
        f"Исходный: _{text}_\n"
        f"───\n"
        f"**{label}:**\n`{res}`"
    )

@client.on(events.NewMessage(pattern=r'!password(?:\s+(\d+))?(?:\s+(simple))?$', from_users='me'))
async def password_cmd(e):
    length = max(4, min(int(e.pattern_match.group(1) or 16), 128))
    sym = not e.pattern_match.group(2)
    pwd = gen_pwd(length, sym)
    strength = "🔴 Слабый" if length < 8 else "🟡 Средний" if length < 12 else "🟢 Сильный" if length < 20 else "💎 Очень сильный"
    entropy = length * (5.95 if sym else 4.7)
    msg = await e.edit(
        f"🔑 **Генератор паролей**\n\n"
        f"Длина: `{length}`\n"
        f"Символы: {'✅ Спецсимволы' if sym else '❌ Только буквы/цифры'}\n"
        f"───\n"
        f"`{pwd}`\n"
        f"───\n"
        f"Сила: {strength}\n"
        f"Энтропия: `{entropy:.1f} бит`\n\n"
        f"_⚠️ Сообщение самоуничтожится через 30с_"
    )
    await asyncio.sleep(30)
    await msg.delete()

@client.on(events.NewMessage(pattern=r'!qr (.+)', from_users='me'))
async def qr_cmd(e):
    text = e.pattern_match.group(1).strip().replace(' ','+')
    await e.edit(
        f"📱 **QR-код**\n\n"
        f"Данные: `{text[:50]}{'…' if len(text)>50 else ''}`\n\n"
        f"🔗 [Открыть QR-код](https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={text})"
    )

@client.on(events.NewMessage(pattern=r'!uuid$', from_users='me'))
async def uuid_cmd(e):
    ids = [str(uuid.uuid4()) for _ in range(5)]
    out = "\n".join(f"`{u}`" for u in ids)
    await e.edit(
        f"🆔 **UUID v4**\n\n"
        f"{out}\n\n"
        f"_5 случайных UUID_"
    )

@client.on(events.NewMessage(pattern=r'!color (#[0-9a-fA-F]{6}|\d+,\d+,\d+)', from_users='me'))
async def color_cmd(e):
    raw = e.pattern_match.group(1).strip()
    if raw.startswith('#'):
        h = raw.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        hex_val = raw.upper()
    else:
        r, g, b = map(int, raw.split(','))
        hex_val = f"#{r:02X}{g:02X}{b:02X}"
    rf, gf, bf = r / 255, g / 255, b / 255
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    lightness = (mx + mn) / 2
    sat = 0 if mx == mn else (mx - mn) / (1 - abs(2 * lightness - 1))
    if mx == mn:
        hue = 0
    elif mx == rf:
        hue = 60 * ((gf - bf) / (mx - mn) % 6)
    elif mx == gf:
        hue = 60 * ((bf - rf) / (mx - mn) + 2)
    else:
        hue = 60 * ((rf - gf) / (mx - mn) + 4)
    preview_url = f"https://www.colorhexa.com/{hex_val.lstrip('#')}"
    block = f"🟥🟧🟨🟩🟦🟪"[hue // 60]
    await e.edit(
        f"🎨 **Цвет** {block}\n\n"
        f"HEX: `{hex_val}`\n"
        f"RGB: `rgb({r}, {g}, {b})`\n"
        f"HSL: `hsl({hue:.0f}°, {sat * 100:.0f}%, {lightness * 100:.0f}%)`\n\n"
        f"🔗 [Превью]({preview_url})"
    )

@client.on(events.NewMessage(pattern=r'!ascii (.+)', from_users='me'))
async def ascii_cmd(e):
    text = e.pattern_match.group(1).strip()
    codes = ' '.join(str(ord(c)) for c in text)
    hex_codes = ' '.join(f"{ord(c):02X}" for c in text)
    back = ''.join(chr(int(x)) for x in codes.split())
    await e.edit(
        f"🔢 **ASCII / Unicode**\n\n"
        f"Текст: _{text}_\n"
        f"───\n"
        f"Dec: `{codes}`\n"
        f"Hex: `{hex_codes}`\n"
        f"───\n"
        f"Обратно: `{back}`"
    )

# ─── УПРАВЛЕНИЕ СООБЩЕНИЯМИ ──────────────────────────────
async def _type_fast(e, text):
    msg = await e.edit("▌")
    for i in range(0, len(text), 2):
        chunk = text[:i+2]
        await msg.edit(chunk + ("▌" if i+2 < len(text) else ""))
        await asyncio.sleep(0.04)
    await msg.edit(text)

async def _type_slow(e, text):
    msg = await e.edit("▌")
    shown = ""
    for ch in text:
        shown += ch
        await msg.edit(shown + "▌")
        pause = 0.3 if ch in '.!?…' else 0.12 if ch in ',;:' else 0.07
        await asyncio.sleep(pause)
    await msg.edit(text)

async def _type_matrix(e, text):
    CHARS = string.ascii_letters + string.digits + "@#%&"
    msg = await e.edit("▓" * len(text))
    for step in range(len(text)):
        parts = list(text[:step])
        for _ in range(len(text) - step):
            parts.append(random.choice(CHARS))
        await msg.edit(''.join(parts))
        await asyncio.sleep(0.07)
    await msg.edit(text)

async def _type_glitch(e, text):
    GLITCH = "░▒▓█▄▀■□▪▫"
    msg = await e.edit("".join(random.choice(GLITCH) for _ in text))
    for _ in range(6):
        glitched = "".join(
            c if random.random() > 0.4 else random.choice(GLITCH)
            for c in text
        )
        await msg.edit(glitched)
        await asyncio.sleep(0.12)
    await msg.edit(text)

async def _type_normal(e, text):
    msg = await e.edit("▌")
    shown = ""
    for i, ch in enumerate(text):
        shown += ch
        if i % 2 == 0 or i == len(text)-1:
            await msg.edit(shown + ("▌" if i < len(text)-1 else ""))
            await asyncio.sleep(0.05)
    await msg.edit(text)

TYPE_MODES = {'fast': _type_fast, 'slow': _type_slow, 'matrix': _type_matrix, 'glitch': _type_glitch}

@client.on(events.NewMessage(pattern=r'!type(?:\s+(fast|slow|matrix|glitch))?\s+(.+)', from_users='me'))
async def type_cmd(e):
    mode = e.pattern_match.group(1) or 'normal'
    text = e.pattern_match.group(2).strip()
    func = TYPE_MODES.get(mode, _type_normal)
    await func(e, text)

@client.on(events.NewMessage(pattern=r'!echo (.+)', from_users='me'))
async def echo_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, e.pattern_match.group(1).strip())

@client.on(events.NewMessage(pattern=r'!bold (.+)', from_users='me'))
async def bold_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, f"**{e.pattern_match.group(1).strip()}**")

@client.on(events.NewMessage(pattern=r'!italic (.+)', from_users='me'))
async def italic_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, f"__{e.pattern_match.group(1).strip()}__")

@client.on(events.NewMessage(pattern=r'!mono (.+)', from_users='me'))
async def mono_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, f"`{e.pattern_match.group(1).strip()}`")

MAX_CLEAN = 100

@client.on(events.NewMessage(pattern=r'!clean(?:\s+(\d+))?$', from_users='me'))
async def clean_cmd(e):
    limit = int(e.pattern_match.group(1) or 10)
    my_id = (await client.get_me()).id
    await e.delete()
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        if msg.out or (msg.from_id and getattr(msg.from_id,'user_id',None)==my_id):
            await msg.delete()
            count += 1
            await asyncio.sleep(0.1)
    info = await client.send_message(e.chat_id, f"✅ Удалено **{count}** своих сообщений")
    await asyncio.sleep(3)
    await info.delete()

@client.on(events.NewMessage(pattern=r'!purge(?:\s+(\d+))?$', from_users='me'))
async def purge_cmd(e):
    limit = min(int(e.pattern_match.group(1) or 10), MAX_CLEAN)
    await e.delete()
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        await msg.delete()
        count += 1
        await asyncio.sleep(0.04)
    info = await client.send_message(e.chat_id, f"⚠️ Удалено **{count}** сообщений")
    await asyncio.sleep(3)
    await info.delete()

@client.on(events.NewMessage(pattern=r'!spam (\d+) (.+)', from_users='me'))
async def spam_cmd(e):
    count, text = int(e.pattern_match.group(1)), e.pattern_match.group(2).strip()
    await e.delete()
    for _ in range(count):
        await client.send_message(e.chat_id, text)
        await asyncio.sleep(0.35)

@client.on(events.NewMessage(pattern=r'!forward (-?\d+)', from_users='me'))
async def forward_cmd(e):
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ **Forward**\n\nОтветьте на сообщение:\n`!forward [chat_id]`")
        return
    try:
        msg = await e.get_reply_message()
        await client.forward_messages(int(e.pattern_match.group(1)), msg)
        await e.edit(f"✅ **Переслано**\n\nВ чат: `{e.pattern_match.group(1)}`")
    except Exception as ex:
        await e.edit(f"❌ **Ошибка**\n\n`{ex}`")

@client.on(events.NewMessage(pattern=r'!pin$', from_users='me'))
async def pin_cmd(e):
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ **Pin**\n\nОтветьте на сообщение, которое нужно закрепить")
        return
    await (await e.get_reply_message()).pin(notify=False)
    await e.delete()

@client.on(events.NewMessage(pattern=r'!unpin$', from_users='me'))
async def unpin_cmd(e):
    if e.reply_to_msg_id:
        await (await e.get_reply_message()).unpin()
    else:
        await client.unpin_message(e.chat_id)
    await e.delete()

@client.on(events.NewMessage(pattern=r'!copyall (\d+) (-?\d+)', from_users='me'))
async def copyall_cmd(e):
    count = int(e.pattern_match.group(1))
    target = int(e.pattern_match.group(2))
    msg = await e.edit(f"⏳ **Копирование...**\n\n0/{count}")
    copied = 0
    async for m in client.iter_messages(e.chat_id, limit=count, reverse=True):
        try:
            await client.forward_messages(target, m)
            copied += 1
            if copied % 5 == 0 or copied == count:
                await msg.edit(f"⏳ **Копирование...**\n\n{copied}/{count}")
            await asyncio.sleep(0.4)
        except Exception:
            pass
    await msg.edit(f"✅ **Копирование завершено**\n\n{copied}/{count} → `{target}`")

@client.on(events.NewMessage(pattern=r'!react (.+)', from_users='me'))
async def react_cmd(e):
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ **Reaction**\n\nОтветьте на сообщение:\n`!react 👍`")
        return
    emoji = e.pattern_match.group(1).strip()
    try:
        await client(SendReactionRequest(
            peer=e.chat_id,
            msg_id=e.reply_to_msg_id,
            reaction=[ReactionEmoji(emoticon=emoji)]
        ))
        await e.delete()
    except Exception as ex:
        await e.edit(f"❌ **Ошибка реакции**\n\n`{ex}`")

# ─── ЗАМЕТКИ И TODO ───────────────────────────────────────
@client.on(events.NewMessage(pattern=r'!save (\S+) (.+)', from_users='me'))
async def save_cmd(e):
    k, v = e.pattern_match.group(1), e.pattern_match.group(2)
    d = await load_json(SAVED_FILE, {})
    d[k] = v
    await write_json(SAVED_FILE, d)
    await e.edit(f"💾 **Сохранено**\n\n`{k}`\n───\n_{v}_")

@client.on(events.NewMessage(pattern=r'!get (\S+)', from_users='me'))
async def get_cmd(e):
    k = e.pattern_match.group(1)
    d = await load_json(SAVED_FILE, {})
    v = d.get(k)
    if v:
        await e.edit(f"📦 **Значение**\n\nКлюч: `{k}`\n───\n_{v}_")
    else:
        await e.edit(f"❌ **Не найдено**\n\nКлюч `{k}` не существует")

@client.on(events.NewMessage(pattern=r'!del (\S+)', from_users='me'))
async def del_cmd(e):
    k = e.pattern_match.group(1)
    d = await load_json(SAVED_FILE, {})
    if k in d:
        val_preview = d[k][:40]
        del d[k]
        await write_json(SAVED_FILE, d)
        await e.edit(f"🗑 **Удалено**\n\n`{k}` — _{val_preview}{'…' if len(val_preview)>=40 else ''}_")
    else:
        await e.edit(f"❌ **Не найдено**\n\nКлюч `{k}` не существует")

@client.on(events.NewMessage(pattern=r'!list$', from_users='me'))
async def list_cmd(e):
    d = await load_json(SAVED_FILE, {})
    if not d:
        await e.edit("📭 **Хранилище**\n\nНет сохранённых данных")
        return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v)>40 else ''}_" for k, v in d.items())
    await e.edit(f"💾 **Хранилище** (`{len(d)}`)\n\n{items}")

@client.on(events.NewMessage(pattern=r'!note (\S+)(?: (.+))?', from_users='me'))
async def note_cmd(e):
    k = e.pattern_match.group(1)
    t = e.pattern_match.group(2) or ""
    if e.reply_to_msg_id:
        r = await e.get_reply_message()
        t = r.text or t
    if not t:
        await e.edit("ℹ️ **Заметки**\n\n`!note <название> <текст>`\nИли ответь на сообщение: `!note <название>`")
        return
    d = await load_json(NOTES_FILE, {})
    d[k] = t
    await write_json(NOTES_FILE, d)
    await e.edit(f"📝 **Заметка сохранена**\n\n`{k}`")

@client.on(events.NewMessage(pattern=r'!getnote (\S+)', from_users='me'))
async def getnote_cmd(e):
    k = e.pattern_match.group(1)
    d = await load_json(NOTES_FILE, {})
    if k in d:
        await e.edit(f"📝 **{k}**\n\n{d[k]}")
    else:
        await e.edit(f"❌ **Заметка не найдена**\n\n`{k}`")

@client.on(events.NewMessage(pattern=r'!delnote (\S+)', from_users='me'))
async def delnote_cmd(e):
    k = e.pattern_match.group(1)
    d = await load_json(NOTES_FILE, {})
    if k in d:
        del d[k]
        await write_json(NOTES_FILE, d)
        await e.edit(f"🗑 **Заметка удалена**\n\n`{k}`")
    else:
        await e.edit(f"❌ **Не найдена**\n\nЗаметка `{k}` не существует")

@client.on(events.NewMessage(pattern=r'!notes$', from_users='me'))
async def notes_cmd(e):
    d = await load_json(NOTES_FILE, {})
    if not d:
        await e.edit("📭 **Заметки**\n\nНет заметок")
        return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v)>40 else ''}_" for k, v in d.items())
    await e.edit(f"📝 **Заметки** (`{len(d)}`)\n\n{items}")

@client.on(events.NewMessage(pattern=r'!todo (.+)', from_users='me'))
async def todo_add_cmd(e):
    task = e.pattern_match.group(1).strip()
    todos = await load_json(TODOS_FILE, [])
    todos.append({'text': task, 'done': False, 'id': int(time.time())})
    await write_json(TODOS_FILE, todos)
    pending = sum(1 for t in todos if not t['done'])
    await e.edit(
        f"✅ **Задача добавлена**\n\n"
        f"`{task}`\n"
        f"───\n"
        f"📋 Всего: `{len(todos)}` | Осталось: `{pending}`"
    )

@client.on(events.NewMessage(pattern=r'!todos$', from_users='me'))
async def todos_cmd(e):
    todos = await load_json(TODOS_FILE, [])
    if not todos:
        await e.edit("📭 **TODO**\n\nСписок задач пуст\n\n`!todo <задача>` — добавить")
        return
    lines = []
    for i, t in enumerate(todos, 1):
        mark = "✅" if t['done'] else "⬜"
        lines.append(f"{mark} {i}. `{t['text']}`")
    done = sum(1 for t in todos if t['done'])
    pending = len(todos) - done
    await e.edit(
        f"📋 **Список задач**\n\n"
        f"{chr(10).join(lines)}\n"
        f"───\n"
        f"✅ `{done}` выполнено | ⬜ `{pending}` осталось"
    )

@client.on(events.NewMessage(pattern=r'!done (\d+)', from_users='me'))
async def done_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = await load_json(TODOS_FILE, [])
    if 0 <= idx < len(todos):
        todos[idx]['done'] = True
        await write_json(TODOS_FILE, todos)
        await e.edit(f"✅ **Выполнено**\n\n`{todos[idx]['text']}`")
    else:
        await e.edit(f"❌ **Ошибка**\n\nЗадача #{idx + 1} не найдена")

@client.on(events.NewMessage(pattern=r'!undone (\d+)', from_users='me'))
async def undone_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = await load_json(TODOS_FILE, [])
    if 0 <= idx < len(todos):
        todos[idx]['done'] = False
        await write_json(TODOS_FILE, todos)
        await e.edit(f"⬜ **Отменено**\n\n`{todos[idx]['text']}`")
    else:
        await e.edit(f"❌ **Ошибка**\n\nЗадача #{idx + 1} не найдена")

@client.on(events.NewMessage(pattern=r'!deltodo (\d+)', from_users='me'))
async def deltodo_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = await load_json(TODOS_FILE, [])
    if 0 <= idx < len(todos):
        removed = todos.pop(idx)
        await write_json(TODOS_FILE, todos)
        await e.edit(f"🗑 **Задача удалена**\n\n`{removed['text']}`")
    else:
        await e.edit(f"❌ **Ошибка**\n\nЗадача #{idx + 1} не найдена")

# ─── AFK ──────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r'!afk(?:\s+(.+))?$', from_users='me'))
async def afk_cmd(e):
    reason = (e.pattern_match.group(1) or '').strip()
    state.set_afk(reason)
    message = "😴 **AFK включён**"
    if reason:
        message += f"\n\n📝 Причина: _{reason}_"
    await e.edit(message)

@client.on(events.NewMessage(pattern=r'!unafk$', from_users='me'))
async def unafk_cmd(e):
    dur = state.clear_afk()
    if dur is not None:
        await e.edit(
            f"☀️ **AFK выключен**\n\n"
            f"Отсутствовал: `{fmt_time(dur)}`"
        )
    else:
        await e.edit("ℹ️ **AFK**\n\nРежим AFK не был включён")

# ─── ИНФОРМАЦИЯ О ЧАТЕ ──────────────────────────────────
@client.on(events.NewMessage(pattern=r'!chatinfo$', from_users='me'))
async def chatinfo_cmd(e):
    chat = await e.get_chat()
    name = getattr(chat, 'title', None) or f"{getattr(chat, 'first_name', '')} {getattr(chat, 'last_name', '')}".strip()
    uname = getattr(chat, 'username', None)
    members = getattr(chat, 'participants_count', None)
    chat_type = type(chat).__name__
    type_labels = {'Chat': 'Группа', 'Channel': 'Канал', 'User': 'Пользователь'}
    type_label = type_labels.get(chat_type, chat_type)
    lines = [
        f"📊 **Информация о чате**\n",
        f"📛 **{name}**",
        f"🆔 `{e.chat_id}`",
        f"🔗 @{uname}" if uname else "🔗 **Username:** —",
        f"👥 **Тип:** `{type_label}`",
    ]
    if members:
        lines.append(f"👤 **Участников:** `{members}`")
    lines.append(f"🔒 **Создатель:** {'✅' if getattr(chat, 'creator', False) else '❌'}")
    await e.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'!members$', from_users='me'))
async def members_cmd(e):
    try:
        p = await client.get_participants(e.chat_id)
        bots = sum(1 for x in p if x.bot)
        admins = sum(1 for x in p if getattr(x, 'admin_rights', None))
        await e.edit(
            f"👥 **Участники чата**\n\n"
            f"👤 **Всего:** `{len(p)}`\n"
            f"👤 **Людей:** `{len(p) - bots}`\n"
            f"🤖 **Ботов:** `{bots}`\n"
            f"👑 **Админов:** `{admins}`"
        )
    except Exception as ex:
        await e.edit(f"❌ **Ошибка**\n\n`{ex}`")

@client.on(events.NewMessage(pattern=r'!admins$', from_users='me'))
async def admins_cmd(e):
    try:
        admins = await client.get_participants(e.chat_id, filter=ChannelParticipantsAdmins())
        lines = [f"👑 **Администраторы** (`{len(admins)}`)\n"]
        for a in admins[:25]:
            name = f"{a.first_name or ''} {a.last_name or ''}".strip()
            tag = f"@{a.username}" if a.username else f"`{a.id}`"
            lines.append(f"• {name} — {tag}")
        if len(admins) > 25:
            lines.append(f"\n_и ещё {len(admins) - 25}_")
        await e.edit("\n".join(lines))
    except Exception as ex:
        await e.edit(f"❌ **Ошибка**\n\n`{ex}`")

@client.on(events.NewMessage(pattern=r'!top(?:\s+(\d+))?$', from_users='me'))
async def top_cmd(e):
    limit = int(e.pattern_match.group(1) or 200)
    msg = await e.edit(f"⏳ **Анализ чата...**\n\nПроверяю последние `{limit}` сообщений")
    cnt, names = defaultdict(int), {}
    async for m in client.iter_messages(e.chat_id, limit=limit):
        if m.sender_id:
            cnt[m.sender_id] += 1
            if m.sender_id not in names:
                s = await m.get_sender()
                if s:
                    n = f"{getattr(s, 'first_name', '') or ''} {getattr(s, 'last_name', '') or ''}".strip()
                    names[m.sender_id] = n or str(m.sender_id)
    top = sorted(cnt.items(), key=lambda x: x[1], reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    unique = len(cnt)
    total = sum(cnt.values())
    lines = [f"🏆 **Топ активных**\n"]
    lines.append(f"📊 Проанализировано: `{total}` сообщ. от `{unique}` участников\n")
    for i, (uid, c) in enumerate(top):
        pct = c / total * 100
        bar = progress_bar(int(pct), 100, 8)
        lines.append(f"{medals[i]} {names.get(uid, uid)} — `{c}` ({pct:.0f}%)")
    await msg.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'!bots$', from_users='me'))
async def bots_cmd(e):
    try:
        bots = await client.get_participants(e.chat_id, filter=ChannelParticipantsBots())
        if not bots:
            await e.edit("🤖 **Боты**\n\nВ этом чате нет ботов")
            return
        lines = [f"🤖 **Боты в чате** (`{len(bots)}`)\n"]
        for b in bots[:20]:
            tag = f"@{b.username}" if b.username else f"`{b.id}`"
            name = f"{b.first_name or ''}".strip()
            lines.append(f"• {name} — {tag}")
        if len(bots) > 20:
            lines.append(f"\n_и ещё {len(bots) - 20}_")
        await e.edit("\n".join(lines))
    except Exception as ex:
        await e.edit(f"❌ **Ошибка**\n\n`{ex}`")

# ─── СБРОС ДАННЫХ ────────────────────────────────────────
@client.on(events.NewMessage(pattern=r'!resetdata$', from_users='me'))
async def resetdata_cmd(e):
    files = [DATA_FILE, SAVED_FILE, NOTES_FILE, TODOS_FILE, STATS_FILE]
    await e.edit("🧹 **Сброс данных...**\n\nУдаляю файлы...")
    deleted = 0
    for f in files:
        if os.path.exists(f):
            os.remove(f)
            deleted += 1
            logger.info(f"Удалён {f}")
    state.auto_reply_enabled = False
    state.auto_reply_text = '💫 Я автоответчик, хозяин скоро ответит! Спасибо за терпение 😘'
    state.ghost_mode = False
    state.afk_start_time = None
    state.afk_reason = ''
    state.save()
    await e.edit(f"🧹 **Все данные сброшены.**\n\nУдалено файлов: `{deleted}`\nСостояние сброшено к заводским настройкам")

# ════════════════════════════════════════════════════════════
# 2. RP-КОМАНДЫ (из модуля rp_commands) – без префикса
# ════════════════════════════════════════════════════════════
# обработка в private_handler

# ════════════════════════════════════════════════════════════
# 3. ГЛАВНЫЙ ОБРАБОТЧИК ЛИЧНЫХ СООБЩЕНИЙ (RP, AFK, автоответчик)
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(func=lambda e: e.is_private))
async def private_handler(event):
    sender = await event.get_sender()
    if not sender:
        return

    logger.info(f"📩 [ЛС] От {sender.first_name} (id:{sender.id}), текст: {event.raw_text[:50]}")
    if event.reply_to_msg_id:
        logger.info(f"↩️ Ответ на сообщение ID:{event.reply_to_msg_id}")

    # ─── RP-КОМАНДЫ (без префикса) ────────────────────────
    if event.reply_to_msg_id:
        try:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.sender_id:
                text = event.raw_text.strip().lower()
                if text in rp_commands.RP_COMMANDS:
                    target_entity = await event.client.get_entity(reply_msg.sender_id)
                    target_name = target_entity.first_name or "пользователь"
                    user_name = sender.first_name or "Кто-то"

                    cmd = rp_commands.RP_COMMANDS[text]
                    action_text = cmd['text'].format(user=user_name, target=target_name)
                    reply_text = cmd['reply']

                    await event.reply(f"{cmd['emoji']} {action_text}\n\n{reply_text}")
                    logger.info(f"✅ RP-действие: {user_name} -> {target_name} ({text})")
                    await bump_stat('cmds')
                    return  # не обрабатываем AFK/автоответчик
        except Exception as e:
            logger.error(f"Ошибка RP: {e}")

    # ─── AFK ────────────────────────────────────────────────────
    uid = sender.id
    now = time.time()
    if state.afk_start_time and now - reply_cooldown.get(f'afk_{uid}', 0) > 60:
        dur = fmt_time(now - state.afk_start_time)
        reason_part = f"\n📝 _{state.afk_reason}_" if state.afk_reason else ""
        reply_cooldown[f'afk_{uid}'] = now
        await event.reply(f"😴 Хозяин AFK уже **{dur}**{reason_part}")

    # ─── АВТООТВЕТЧИК ──────────────────────────────────────────
    if state.auto_reply_enabled and now - reply_cooldown.get(uid, 0) > 10:
        reply_cooldown[uid] = now
        await asyncio.sleep(1)
        await event.reply(state.auto_reply_text)

# ════════════════════════════════════════════════════════════
# 4. КОМАНДА !rphelp (без префикса, но с !)
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'^!rphelp$', func=lambda e: e.is_private))
async def rphelp_cmd(event):
    sender = await event.get_sender()
    if not sender:
        return
    total = sum(len(rp_commands.get_category_commands(c)) for c in rp_commands.get_all_categories())
    blocks = []
    for category in rp_commands.get_all_categories():
        cmds = rp_commands.get_category_commands(category)
        if cmds:
            cat_emoji = {'интимные': '🔞', 'эротические': '🌹', 'агрессивные': '👊', 'романтические': '💕', 'дружеские': '🤝'}
            emoji = cat_emoji.get(category, '•')
            cmd_line = "  ".join(f"`{c}`" for c in cmds)
            blocks.append(f"{emoji} **{category.upper()}** ({len(cmds)})\n{cmd_line}")
    await event.reply(
        f"🎭 **RP-команды**\n\n"
        f"Ролевые действия в ответ на сообщение в ЛС.\n"
        f"Всего команд: `{total}`\n\n"
        f"{chr(10).join(blocks)}\n\n"
        f"💡 _Напиши команду в ответ на сообщение — бот выполнит действие_"
    )
    await bump_stat('cmds')

# ════════════════════════════════════════════════════════════
# 5. СТЕЛС-КОМАНДЫ (префикс !)
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'^!cover$', from_users='me'))
async def cover_cmd(e):
    state.set_cover()
    await e.edit(f"🥷 **Cover режим** {'✅ ВКЛЮЧЁН' if state.cover_enabled else '❌ ВЫКЛЮЧЕН'}")

@client.on(events.NewMessage(pattern=r'^!silent$', from_users='me'))
async def silent_cmd(e):
    state.set_silent()
    await e.edit(f"🔇 **Silent режим** {'✅ ВКЛЮЧЁН' if state.silent_enabled else '❌ ВЫКЛЮЧЕН'}")

@client.on(events.NewMessage(pattern=r'^!shadow$', from_users='me'))
async def shadow_cmd(e):
    state.set_shadow()
    await e.edit(f"👤 **Shadow режим** {'✅ ВКЛЮЧЁН' if state.shadow_enabled else '❌ ВЫКЛЮЧЕН'}")

@client.on(events.NewMessage(pattern=r'^!lock$', from_users='me'))
async def lock_cmd(e):
    state.set_lock()
    await e.edit(f"🔒 **Lock режим** {'✅ ВКЛЮЧЁН' if state.lock_enabled else '❌ ВЫКЛЮЧЕН'}")

@client.on(events.NewMessage(pattern=r'^!mute$', from_users='me'))
async def mute_cmd(e):
    state.set_mute()
    await e.edit(f"🔇 **Mute режим** {'✅ ВКЛЮЧЁН' if state.mute_enabled else '❌ ВЫКЛЮЧЕН'}")

@client.on(events.NewMessage(pattern=r'^!hide$', from_users='me'))
async def hide_cmd(e):
    state.set_hide()
    await e.edit(f"🕵️ **Hide режим** {'✅ ВКЛЮЧЁН' if state.hide_enabled else '❌ ВЫКЛЮЧЕН'}")

@client.on(events.NewMessage(pattern=r'^!state$', from_users='me'))
async def state_cmd(e):
    status = (
        f"🥷 **Стелс-режимы**\n\n"
        f"Cover:  {'✅' if state.cover_enabled else '❌'}\n"
        f"Silent: {'✅' if state.silent_enabled else '❌'}\n"
        f"Shadow: {'✅' if state.shadow_enabled else '❌'}\n"
        f"Lock:   {'✅' if state.lock_enabled else '❌'}\n"
        f"Mute:   {'✅' if state.mute_enabled else '❌'}\n"
        f"Hide:   {'✅' if state.hide_enabled else '❌'}"
    )
    await e.edit(status)

# ════════════════════════════════════════════════════════════
# 6. HELP И COMMANDS (обновлены)
# ════════════════════════════════════════════════════════════

COMMANDS_LIST = {
    'основные': [
        '!sleep', '!wake', '!setreply', '!status', '!time', '!ping',
        '!id', '!info', '!restart', '!ghost', '!resetdata'
    ],
    'профиль': [
        '!me', '!avatar', '!name', '!lastname', '!bio', '!whois', '!username_check'
    ],
    'игры': [
        '!dice', '!dart', '!basket', '!football', '!bowling', '!casino',
        '!coin', '!rand', '!8ball', '!rps', '!slot', '!lucky', '!choose', '!quiz'
    ],
    'утилиты': [
        '!calc', '!remind', '!search', '!shorten', '!weather', '!translate',
        '!base64', '!hash', '!morse', '!caesar', '!vigenere', '!password',
        '!qr', '!uuid', '!color', '!ascii'
    ],
    'сообщения': [
        '!type', '!echo', '!bold', '!italic', '!mono',
        '!clean', '!purge', '!spam', '!forward', '!pin', '!unpin',
        '!copyall', '!react'
    ],
    'заметки': [
        '!save', '!get', '!del', '!list',
        '!note', '!getnote', '!delnote', '!notes',
        '!todo', '!todos', '!done', '!undone', '!deltodo'
    ],
    'afk': ['!afk', '!unafk'],
    'инфо': ['!chatinfo', '!members', '!admins', '!top', '!bots'],
    'стелс': ['!cover', '!silent', '!shadow', '!state', '!lock', '!mute', '!hide'],
    # 'rp' удалена из списка, чтобы не показываться в !commands
}

EMOJI_MAP = {
    'основные': '⚙️', 'профиль': '👤', 'игры': '🎮', 'утилиты': '🛠',
    'сообщения': '✉️', 'заметки': '📦', 'afk': '😴', 'инфо': '📊',
    'стелс': '🥷', 'rp': '🎭'
}

DESC_MAP = {
    'основные': 'автоответчик, статус, время, перезагрузка',
    'профиль': 'управление профилем и проверка пользователей',
    'игры': 'анимации, 8ball, КНБ, слоты, викторина',
    'утилиты': 'калькулятор, хэши, шифры, пароли, QR',
    'сообщения': 'печать, очистка, пересылка, реакции',
    'заметки': 'заметки, список задач, хранилище',
    'afk': 'режим отсуствия',
    'инфо': 'участники, админы, топ активности',
    'стелс': 'режимы скрытности и защиты',
    'rp': 'ролевые команды (в ответ на сообщение)'
}

HELP_CATS = {
    'основные': (
        "⚙️ **ОСНОВНЫЕ**\n\n"
        "▸ `  !sleep  ` — включить автоответчик\n"
        "▸ `  !wake   ` — выключить\n"
        "▸ `  !setreply <текст>` — задать текст ответа\n"
        "▸ `  !status ` — состояние: AFK, авт.ответ, аптайм\n"
        "▸ `  !time   ` — текущие дата и время\n"
        "▸ `  !ping   ` — задержка до серверов TG\n"
        "▸ `  !id     ` — ID чата, сообщения, пользователя\n"
        "▸ `  !info   ` — информация о боте и аккаунте\n"
        "▸ `  !restart` — перезагрузка бота\n"
        "▸ `  !ghost  ` — команды самоуничтожаются\n"
        "▸ `  !resetdata` — сбросить все данные ⚠️"
    ),
    'профиль': (
        "👤 **ПРОФИЛЬ**\n\n"
        "▸ `  !me           ` — мой профиль\n"
        "▸ `  !avatar       ` — показать аватарку\n"
        "▸ `  !name <имя>   ` — сменить имя\n"
        "▸ `  !lastname <фам>` — сменить фамилию\n"
        "▸ `  !bio <текст>  ` — обновить «о себе»\n"
        "▸ `  !whois @ник   ` — информация о пользователе\n"
        "▸ `  !username_check @ник` — проверить занят ли username"
    ),
    'игры': (
        "🎮 **ИГРЫ**\n\n"
        "▸ `  !dice / !dart / !basket / !football / !bowling / !casino` — анимированные emoji\n"
        "▸ `  !coin      ` — подбросить монетку\n"
        "▸ `  !rand [a] [b]` — случайное число oт a до b\n"
        "▸ `  !8ball [вопрос]` — магический шар\n"
        "▸ `  !rps [к/н/б]` — камень-ножницы-бумага\n"
        "▸ `  !slot      ` — слот-машина\n"
        "▸ `  !lucky     ` — индекс удачи сегодня\n"
        "▸ `  !choose A, B` — выбрать из вариантов\n"
        "▸ `  !quiz      ` — случайный вопрос викторины"
    ),
    'утилиты': (
        "🛠 **УТИЛИТЫ**\n\n"
        "▸ `  !calc <выражение>` — калькулятор\n"
        "▸ `  !remind <сек> <текст>` — напомнить через N сек\n"
        "▸ `  !search <запрос>` — ссылки на поисковики\n"
        "▸ `  !shorten <url>` — сократить ссылку\n"
        "▸ `  !weather <город>` — ссылки на погоду\n"
        "▸ `  !translate <текст>` — ссылки на перевод\n"
        "▸ `  !base64 encode/decode <текст>`\n"
        "▸ `  !hash <текст>` — MD5, SHA1/256/512\n"
        "▸ `  !morse <текст>` — азбука Морзе\n"
        "▸ `  !caesar encode/decode <сдвиг> <текст>`\n"
        "▸ `  !vigenere encode/decode <ключ> <текст>`\n"
        "▸ `  !password [длина] [simple]` — генератор паролей\n"
        "▸ `  !qr <текст>` — QR-код\n"
        "▸ `  !uuid     ` — 5 случайных UUID\n"
        "▸ `  !color <#HEX|R,G,B>` — HEX/HSL цвета\n"
        "▸ `  !ascii <текст>` — ASCII коды символов"
    ),
    'сообщения': (
        "✉️ **СООБЩЕНИЯ**\n\n"
        "▸ `  !type [fast|slow|matrix|glitch] <текст>` — анимация печати\n"
        "▸ `  !echo <текст>` — повторить текст в чат\n"
        "▸ `  !bold / !italic / !mono <текст>` — форматирование\n"
        "▸ `  !clean [n]` — удалить свои N сообщений\n"
        "▸ `  !purge [n]` — удалить любые N сообщений\n"
        "▸ `  !spam <n> <текст>` — спам N сообщений\n"
        "▸ `  !forward <chat_id>` — переслать ответное сообщение\n"
        "▸ `  !pin  ` — закрепить ответное сообщение\n"
        "▸ `  !unpin` — открепить\n"
        "▸ `  !copyall <n> <chat_id>` — скопировать N сообщений\n"
        "▸ `  !react <эмодзи>` — поставить реакцию"
    ),
    'заметки': (
        "📦 **ЗАМЕТКИ И TODO**\n\n"
        "📁 **Хранилище**\n"
        "▸ `  !save <ключ> <значение>` — сохранить\n"
        "▸ `  !get <ключ>` — получить\n"
        "▸ `  !del <ключ>` — удалить\n"
        "▸ `  !list` — все сохранённые\n\n"
        "📝 **Заметки**\n"
        "▸ `  !note <название> <текст>` — создать\n"
        "▸ `  !getnote <название>` — открыть\n"
        "▸ `  !delnote <название>` — удалить\n"
        "▸ `  !notes` — список заметок\n\n"
        "✅ **TODO**\n"
        "▸ `  !todo <задача>` — добавить\n"
        "▸ `  !todos` — список задач\n"
        "▸ `  !done <N>` — отметить выполненным\n"
        "▸ `  !undone <N>` — снять отметку\n"
        "▸ `  !deltodo <N>` — удалить задачу"
    ),
    'afk': (
        "😴 **AFK**\n\n"
        "▸ `  !afk [причина]` — уйти в AFK\n"
        "▸ `  !unafk` — вернуться\n\n"
        "_Когда кто-то пишет в ЛС, бот ответит что ты AFK_\n"
        "_и сколько времени прошло._"
    ),
    'инфо': (
        "📊 **ИНФОРМАЦИЯ О ЧАТЕ**\n\n"
        "▸ `  !chatinfo` — данные о текущем чате\n"
        "▸ `  !members` — сколько участников и ботов\n"
        "▸ `  !admins` — список администраторов\n"
        "▸ `  !top [n]` — топ активных за n сообщений\n"
        "▸ `  !bots` — список ботов в чате"
    ),
    'rp': (
        "🎭 **RP-КОМАНДЫ**\n\n"
        "Ролевые действия работают **в ответ на сообщение** в ЛС.\n"
        "Напиши одно слово в ответ — бот выполнит действие.\n\n"
        "┌ **Пример**\n"
        "└ Пользователь: _«Привет!»_\n"
        "  Ты (ответ): `обнять`\n"
        "  → _Бот: 🤗 {твоё имя} обнял(а) {собеседника}_\n\n"
        "📋 **Полный список:** `!rphelp`"
    ),
    'стелс': (
        "🥷 **СТЕЛС-РЕЖИМЫ**\n\n"
        "▸ `  !cover   ` — скрыть аватар/био\n"
        "▸ `  !silent  ` — невидимость (без видимых ответов)\n"
        "▸ `  !shadow  ` — автоудаление команд владельца\n"
        "▸ `  !lock    ` — блокировка аватара (замена на плейсхолдер)\n"
        "▸ `  !mute    ` — скрывать приветственные сообщения\n"
        "▸ `  !hide    ` — включить фейковые стратегии\n"
        "▸ `  !state   ` — показать статус всех режимов"
    )
}

@client.on(events.NewMessage(pattern=r'!help(?:\s+(.+))?$', from_users='me'))
async def help_cmd(e):
    cat = (e.pattern_match.group(1) or '').strip().lower()

    if cat:
        if cat not in HELP_CATS:
            cats_list = "\n".join(
                f"{EMOJI_MAP.get(c, '•')}  `!help {c}`  —  _{DESC_MAP.get(c, '')}_"
                for c in HELP_CATS
            )
            await e.edit(
                f"❌ **«{cat}» — нет такой категории**\n\n"
                f"📚 **Доступные категории:**\n{cats_list}\n\n"
                f"💡 _Напиши `!help <категория>`_"
            )
            await bump_stat('cmds')
            return

        text = HELP_CATS[cat]
        cmds = COMMANDS_LIST.get(cat, [])
        if cmds:
            text += "\n\n──── ⋆ ────\n" + "\n".join(f"  `{cmd}`" for cmd in cmds)
        await e.edit(text)
        await bump_stat('cmds')
        return

    # --- Главное меню (без аргументов) ---
    # Формируем строки в точном соответствии с желанием пользователя
    lines = [
        "📚 **UserBot Help**",
        "",
        "Выбери категорию — скопируй команду и отправь:",
        "",
        "⚙️ Основные → `!help основные`",
        "👤 Профиль → `!help профиль`",
        "🎮 Игры → `!help игры`",
        "🛠 Утилиты → `!help утилиты`",
        "✉️ Сообщения → `!help сообщения`",
        "📦 Заметки → `!help заметки`",
        "😴 Afk → `!help afk`",
        "📊 Инфо → `!help инфо`",
        "🎭 Rp → `!help rp`",
        "🥷 Стелс → `!help стелс`",
        "",
        "💡 Или просто `!commands` — все команды списком"
    ]
    await e.edit("\n".join(lines))
    await bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!commands$', from_users='me'))
async def commands_cmd(e):
    total_cmds = sum(len(cmds) for cmds in COMMANDS_LIST.values())
    blocks = []
    for cat, cmds in COMMANDS_LIST.items():
        emoji = EMOJI_MAP.get(cat, '•')
        cmd_line = "  ".join(f"`{cmd}`" for cmd in cmds)
        blocks.append(f"{emoji}  **{cat.capitalize()}**  · `{len(cmds)}`")
        blocks.append(cmd_line)
    await e.edit(
        "📋  **Все команды**  ·  всего `{total}`\n\n{blocks}".format(
            total=total_cmds,
            blocks="\n".join(blocks)
        )
    )
    await bump_stat('cmds')

# ════════════════════════════════════════════════════════════
# 7. ЗАПУСК
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🚀 Запуск UserBot (с RP-командами)...")
    Thread(target=run_web, daemon=True).start()
    client.start()
    print("✅ Бот запущен! Логи в консоли.")
    client.run_until_disconnected()
