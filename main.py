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
import uuid
import aiohttp
from collections import defaultdict
from functools import wraps

from telethon import TelegramClient, events, utils
from telethon.tl.types import (
    InputMediaDice, MessageEntityMentionName,
    ChannelParticipantsAdmins, ChannelParticipantsBots,
    ReactionEmoji
)
from telethon.tl.custom import Button  # для switch_inline кнопок
from telethon.tl.functions.messages import SendReactionRequest, GetHistoryRequest
from telethon.tl.functions.account import UpdateProfileRequest
from flask import Flask
from threading import Thread

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Конфигурация ──────────────────────────────────────────
API_ID  = os.environ.get('API_ID')
API_HASH = os.environ.get('API_HASH')
PORT = int(os.environ.get('PORT', 8080))
STRING_SESSION = os.environ.get('STRING_SESSION')  # если задана, используем её

DATA_FILE  = 'userbot_data.json'
SAVED_FILE = 'saved_data.json'
NOTES_FILE = 'notes_data.json'
TODOS_FILE = 'todos_data.json'
STATS_FILE = 'stats_data.json'

# ─── Класс для управления состоянием бота ────────────────
class BotState:
    def __init__(self):
        self.auto_reply_enabled = False
        self.auto_reply_text = '💫 Я автоответчик, хозяин скоро ответит! Спасибо за терпение 😘'
        self.ghost_mode = False
        self.afk_start_time = None
        self.afk_reason = ''
        self.bot_start_time = time.time()
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
                    'afk_reason': self.afk_reason
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

def load_json(path, default=None):
    if default is None:
        default = {} if not path.endswith('todos') else []
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default
    return default

def write_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка записи {path}: {e}")

# ─── Защита от флуда ────────────────────────────────────────
command_cooldown = defaultdict(float)
COOLDOWN_SEC = 1.5

def bump_stat(key, n=1):
    d = load_json(STATS_FILE, {})
    d[key] = d.get(key, 0) + n
    write_json(STATS_FILE, d)

# ─── Глобальное состояние ──────────────────────────────────
state = BotState()

# ─── Клиент Telegram (с поддержкой строки сессии) ──────────
def create_client():
    if STRING_SESSION:
        from telethon.sessions import StringSession
        session = StringSession(STRING_SESSION)
        return TelegramClient(session, int(API_ID), API_HASH)
    else:
        return TelegramClient('my_userbot', int(API_ID), API_HASH)

client = create_client()

# ─── Flask веб-сервер ─────────────────────────────────────
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 UserBot работает 24/7!"

def run_web():
    app.run(host='0.0.0.0', port=PORT)

# ════════════════════════════════════════════════════════════
# 1. ОСНОВНЫЕ КОМАНДЫ
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/sleep$', from_users='me'))
async def sleep_cmd(e):
    state.toggle_auto_reply(True)
    await e.edit('💤 Автоответчик **ВКЛЮЧЕН**.')

@client.on(events.NewMessage(pattern=r'/wake$', from_users='me'))
async def wake_cmd(e):
    state.toggle_auto_reply(False)
    await e.edit('☀️ Автоответчик **ВЫКЛЮЧЕН**.')

@client.on(events.NewMessage(pattern=r'/setreply (.+)', from_users='me'))
async def setreply_cmd(e):
    text = e.pattern_match.group(1).strip()
    state.set_auto_reply_text(text)
    await e.edit(f"✅ Текст автоответчика:\n_{text}_")

@client.on(events.NewMessage(pattern=r'/status$', from_users='me'))
async def status_cmd(e):
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    s = load_json(STATS_FILE, {})
    afk_status = f"✅ {state.afk_reason or 'без причины'}" if state.afk_start_time else "❌"
    await e.edit(
        f"📊 **Статус UserBot**\n\n"
        f"👤 {me.first_name} {me.last_name or ''}\n"
        f"💬 Чатов: `{len(dialogs)}`\n"
        f"🤖 Автоответчик: {'💤 Вкл' if state.auto_reply_enabled else '☀️ Выкл'}\n"
        f"👻 Ghost-режим: {'✅' if state.ghost_mode else '❌'}\n"
        f"😴 AFK: {afk_status}\n"
        f"⏱ Аптайм: `{state.uptime}`\n"
        f"📨 Команд выполнено: `{s.get('cmds',0)}`"
    )

@client.on(events.NewMessage(pattern=r'/time$', from_users='me'))
async def time_cmd(e):
    now = datetime.datetime.now()
    utc = datetime.datetime.utcnow()
    week_days = ['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье']
    day_of_year = now.timetuple().tm_yday
    week_num = now.isocalendar()[1]
    await e.edit(
        f"🕐 **Время и дата**\n\n"
        f"🏠 Локальное: `{now.strftime('%H:%M:%S')}`\n"
        f"🌍 UTC: `{utc.strftime('%H:%M:%S')}`\n"
        f"📅 Дата: `{now.strftime('%d.%m.%Y')}`\n"
        f"📆 День: **{week_days[now.weekday()]}**\n"
        f"📊 День года: `{day_of_year}/365`\n"
        f"📋 Неделя: `#{week_num}`"
    )

@client.on(events.NewMessage(pattern=r'/ping$', from_users='me'))
async def ping_cmd(e):
    t0 = time.monotonic()
    await e.edit("🏓 ...")
    ms = (time.monotonic() - t0) * 1000
    q = "🟢 Отлично" if ms < 150 else "🟡 Нормально" if ms < 400 else "🔴 Высокая"
    await e.edit(f"🏓 **Понг!**\n⚡ Задержка: `{ms:.1f} мс`\n📶 Качество: {q}")

@client.on(events.NewMessage(pattern=r'/id$', from_users='me'))
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

@client.on(events.NewMessage(pattern=r'/info$', from_users='me'))
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
        f"⏱ Аптайм: `{state.uptime}`\n"
        f"⚡ Статус: **Активен** ✅"
    )

@client.on(events.NewMessage(pattern=r'/restart$', from_users='me'))
async def restart_cmd(e):
    await e.edit('🔄 Перезагрузка...')
    await asyncio.sleep(2)
    await client.disconnect()
    os._exit(0)

@client.on(events.NewMessage(pattern=r'/ghost$', from_users='me'))
async def ghost_cmd(e):
    state.toggle_ghost()
    if state.ghost_mode:
        await e.edit("👻 **Ghost-режим ВКЛЮЧЁН** — команды удаляются мгновенно")
        await asyncio.sleep(2)
        await e.delete()
    else:
        await e.edit("👁 **Ghost-режим ВЫКЛЮЧЕН**")

# ════════════════════════════════════════════════════════════
# 2. ПРОФИЛЬ
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/me$', from_users='me'))
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

@client.on(events.NewMessage(pattern=r'/avatar$', from_users='me'))
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

@client.on(events.NewMessage(pattern=r'/name (.+)', from_users='me'))
async def name_cmd(e):
    n = e.pattern_match.group(1).strip()
    await client.edit_profile(first_name=n)
    await e.edit(f"✅ Имя → **{n}**")

@client.on(events.NewMessage(pattern=r'/lastname(?:\s+(.+))?$', from_users='me'))
async def lastname_cmd(e):
    n = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(last_name=n)
    await e.edit(f"✅ Фамилия → **{n}**" if n else "✅ Фамилия удалена")

@client.on(events.NewMessage(pattern=r'/bio(?:\s+(.+))?$', from_users='me'))
async def bio_cmd(e):
    t = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(about=t)
    await e.edit(f"✅ Био → _{t}_" if t else "✅ Био очищено")

@client.on(events.NewMessage(pattern=r'/whois (.+)', from_users='me'))
async def whois_cmd(e):
    target = e.pattern_match.group(1).strip().lstrip('@')
    try:
        ent = await client.get_entity(target)
        name = f"{getattr(ent,'first_name','') or ''} {getattr(ent,'last_name','') or ''}".strip() \
               or getattr(ent,'title','?')
        uname = f"@{ent.username}" if getattr(ent,'username',None) else "нет"
        bot_ = "✅" if getattr(ent,'bot',False) else "❌"
        ver = "✅" if getattr(ent,'verified',False) else "❌"
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

@client.on(events.NewMessage(pattern=r'/username_check (.+)', from_users='me'))
async def username_check_cmd(e):
    uname = e.pattern_match.group(1).strip().lstrip('@')
    try:
        ent = await client.get_entity(uname)
        name = getattr(ent,'first_name',None) or getattr(ent,'title','?')
        await e.edit(f"🔍 @{uname}\n✅ **Занят**\n👤 {name}\n🆔 `{ent.id}`")
    except:
        await e.edit(f"🔍 @{uname}\n✅ **Свободен**")

# ════════════════════════════════════════════════════════════
# 3. ИГРЫ И РАЗВЛЕЧЕНИЯ
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/dice$', from_users='me'))
async def dice_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎲'))

@client.on(events.NewMessage(pattern=r'/dart$', from_users='me'))
async def dart_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎯'))

@client.on(events.NewMessage(pattern=r'/basket$', from_users='me'))
async def basket_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🏀'))

@client.on(events.NewMessage(pattern=r'/football$', from_users='me'))
async def football_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('⚽'))

@client.on(events.NewMessage(pattern=r'/bowling$', from_users='me'))
async def bowling_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎳'))

@client.on(events.NewMessage(pattern=r'/casino$', from_users='me'))
async def casino_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎰'))

@client.on(events.NewMessage(pattern=r'/coin$', from_users='me'))
async def coin_cmd(e):
    sides = ["Орёл 🦅", "Решка 💰"]
    flips = random.randint(3, 9)
    r = random.choice(sides)
    await e.edit(f"🪙 Монета вращается {flips} раз...\n\nРезультат: **{r}**")

@client.on(events.NewMessage(pattern=r'/rand(?:\s+(-?\d+)(?:\s+(-?\d+))?)?$', from_users='me'))
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

@client.on(events.NewMessage(pattern=r'/8ball(?:\s+(.+))?$', from_users='me'))
async def eightball_cmd(e):
    ANSWERS = {
        'pos': [
            ("Определённо да", "✅", "Вселенная согласна с тобой."),
            ("Без сомнений", "💯", "Это решено раньше, чем ты спросил."),
            ("Скорее всего да", "👍", "Всё складывается в твою пользу."),
            ("Хорошие перспективы", "🌟", "Будущее выглядит светлым."),
            ("Знаки говорят «да»", "🔮", "Мистические силы на твоей стороне."),
            ("Всё указывает на «да»", "💫", "Судьба уже всё решила."),
            ("Да, и поскорее", "🚀", "Не медли — действуй прямо сейчас."),
            ("Абсолютно точно", "🏆", "Лучшего ответа не существует."),
            ("Это неизбежно", "⚡", "Ничто не остановит это."),
            ("Да, если сделаешь шаг", "🦶", "Действие — ключ к результату."),
            ("Вселенная шепчет: да", "🌌", "Даже звёзды кивают."),
            ("Смело иди вперёд", "🎯", "Ты уже знал ответ — я лишь подтверждаю."),
        ],
        'neu': [
            ("Пока не ясно", "🤔", "Туман будущего слишком густой."),
            ("Спроси позже", "⏰", "Момент ещё не настал."),
            ("Не могу предсказать", "🌫", "Слишком много переменных."),
            ("Сосредоточься и повтори", "🧘", "Твой разум мешает ответу."),
            ("Лучше не рассказывать", "🤫", "Некоторые тайны лучше хранить."),
            ("Трудно сказать", "😶", "Даже я не всесилен."),
            ("Возможно, но не сейчас", "🌙", "Подожди подходящего момента."),
            ("Ответ где-то рядом", "🔭", "Смотри внимательнее вокруг себя."),
        ],
        'neg': [
            ("Мой ответ — нет", "🚫", "Прими это спокойно."),
            ("Перспективы не очень", "😕", "Стоит пересмотреть планы."),
            ("Весьма сомнительно", "🙄", "Интуиция говорит «осторожно»."),
            ("Точно нет", "💀", "Даже не думай об этом."),
            ("Не рассчитывай", "❌", "Лучше найди другой путь."),
            ("Категорически нет", "🔴", "Вселенная против."),
            ("Всё против этого", "⛈", "Сейчас не лучшее время."),
            ("Откажись от идеи", "🗑", "Это дорога в никуда."),
            ("Шансы ничтожны", "🎰", "Даже удача отвернулась."),
        ],
    }

    question = (e.pattern_match.group(1) or '').strip()
    spin = ["🎱", "🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘", "🎱"]
    msg = await e.edit("🎱 Шар вращается...")
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

@client.on(events.NewMessage(pattern=r'/rps(?:\s+(.+))?$', from_users='me'))
async def rps_cmd(e):
    MAP = {'к':'🪨 Камень','камень':'🪨 Камень','н':'✂️ Ножницы','ножницы':'✂️ Ножницы','б':'📄 Бумага','бумага':'📄 Бумага'}
    BOT = ['🪨 Камень','✂️ Ножницы','📄 Бумага']
    WIN = {'🪨 Камень':'✂️ Ножницы','✂️ Ножницы':'📄 Бумага','📄 Бумага':'🪨 Камень'}
    arg = (e.pattern_match.group(1) or '').lower().strip()
    if not arg or arg not in MAP:
        await e.edit("✊✌️🖐 `/rps камень` / `ножницы` / `бумага` (или `к`/`н`/`б`)")
        return
    uc, bc = MAP[arg], random.choice(BOT)
    if uc == bc: res = "🤝 **Ничья!**"
    elif WIN[uc] == bc: res = "🏆 **Ты победил!**"
    else: res = "💀 **Бот победил!**"
    await e.edit(f"✊✌️🖐 **КНБ**\n\n👤 Ты: {uc}\n🤖 Бот: {bc}\n\n{res}")

@client.on(events.NewMessage(pattern=r'/slot$', from_users='me'))
async def slot_cmd(e):
    SYM = ['🍒','🍋','🍊','🍇','🍉','⭐','💎','7️⃣','🔔','🍀']
    msg = await e.edit("🎰 [ ▓ | ▓ | ▓ ]")
    for _ in range(4):
        s = [random.choice(SYM) for _ in range(3)]
        await msg.edit(f"🎰 [ {s[0]} | {s[1]} | {s[2]} ]")
        await asyncio.sleep(0.3)
    s = [random.choice(SYM) for _ in range(3)]
    if s[0] == s[1] == s[2]:
        res = "💰💰💰 **ДЖЕКПОТ!**" if s[0] in ('💎','7️⃣') else "🎊 **Выигрыш! Три одинаковых!**"
    elif len(set(s)) < 3:
        res = "😅 Почти! Два одинаковых — ещё раз!"
    else:
        res = "💸 Не повезло. Попробуй снова!"
    await msg.edit(f"🎰 [ {s[0]} | {s[1]} | {s[2]} ]\n\n{res}")

@client.on(events.NewMessage(pattern=r'/lucky$', from_users='me'))
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
    msg = next(v for (a,b),v in tips.items() if a <= pct <= b)
    await e.edit(f"🔮 **Индекс удачи**\n\n[{bar}] **{pct}%**\n\n{msg}")

@client.on(events.NewMessage(pattern=r'/choose (.+)', from_users='me'))
async def choose_cmd(e):
    raw = e.pattern_match.group(1)
    opts = [o.strip() for o in re.split(r'[,|/]', raw) if o.strip()]
    if len(opts) < 2:
        await e.edit("ℹ️ Перечисли варианты через запятую: `/choose пицца, суши, бургер`")
        return
    winner = random.choice(opts)
    listed = "\n".join(f"{'➡️' if o==winner else '  •'} {o}" for o in opts)
    await e.edit(f"🤔 **Выбираю из {len(opts)} вариантов...**\n\n{listed}\n\n✅ **Выбор: {winner}**")

@client.on(events.NewMessage(pattern=r'/quiz$', from_users='me'))
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

# ════════════════════════════════════════════════════════════
# 4. УТИЛИТЫ
# ════════════════════════════════════════════════════════════

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

@client.on(events.NewMessage(pattern=r'/calc (.+)', from_users='me'))
async def calc_cmd(e):
    expr = e.pattern_match.group(1).strip()
    r = await safe_eval(expr)
    if r is not None:
        await e.edit(f"🧮 `{expr}` = **{r}**")
    else:
        await e.edit("❌ Ошибка выражения. Разрешены: `+ - * / % sqrt sin cos tan log abs pow pi e factorial ceil floor round`")

async def send_reminder(chat_id, msg, delay):
    await asyncio.sleep(delay)
    try:
        await client.send_message(chat_id, f"⏰ **НАПОМИНАНИЕ:**\n{msg}")
    except Exception as e:
        logger.error(f"Ошибка напоминания: {e}")

@client.on(events.NewMessage(pattern=r'/remind (\d+)\s+(.+)', from_users='me'))
async def remind_cmd(e):
    delay = int(e.pattern_match.group(1))
    text = e.pattern_match.group(2).strip()
    await e.edit(f"⏰ Напоминание через **{fmt_time(delay)}**\n📝 _{text}_")
    asyncio.create_task(send_reminder(e.chat_id, text, delay))

@client.on(events.NewMessage(pattern=r'/search (.+)', from_users='me'))
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

@client.on(events.NewMessage(pattern=r'/shorten (.+)', from_users='me'))
async def shorten_cmd(e):
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
    except Exception:
        await e.edit("❌ Ошибка. Проверь URL.")

@client.on(events.NewMessage(pattern=r'/weather (.+)', from_users='me'))
async def weather_cmd(e):
    city = e.pattern_match.group(1).strip()
    enc = city.replace(' ','+')
    await e.edit(
        f"🌤️ **Погода: {city}**\n\n"
        f"• [wttr.in](https://wttr.in/{enc})\n"
        f"• [OpenWeatherMap](https://openweathermap.org/find?q={enc})\n"
        f"• [Weather.com](https://weather.com/ru-RU/weather/today/l/{enc})"
    )

@client.on(events.NewMessage(pattern=r'/translate (.+)', from_users='me'))
async def translate_cmd(e):
    text = e.pattern_match.group(1).strip()
    enc = text.replace(' ','%20')
    await e.edit(
        f"🌐 **Перевод:** _{text}_\n\n"
        f"• [RU→EN](https://translate.google.com/?sl=ru&tl=en&text={enc})\n"
        f"• [EN→RU](https://translate.google.com/?sl=en&tl=ru&text={enc})\n"
        f"• [Auto→RU](https://translate.google.com/?sl=auto&tl=ru&text={enc})"
    )

@client.on(events.NewMessage(pattern=r'/base64 (encode|decode) (.+)', from_users='me'))
async def base64_cmd(e):
    mode, text = e.pattern_match.group(1), e.pattern_match.group(2).strip()
    try:
        if mode == 'encode':
            res = base64.b64encode(text.encode()).decode()
            await e.edit(f"🔐 **Base64 encode:**\n`{res}`")
        else:
            res = base64.b64decode(text.encode()).decode()
            await e.edit(f"🔓 **Base64 decode:**\n`{res}`")
    except Exception:
        await e.edit("❌ Ошибка. Проверь данные.")

@client.on(events.NewMessage(pattern=r'/hash (.+)', from_users='me'))
async def hash_cmd(e):
    text = e.pattern_match.group(1).strip().encode()
    await e.edit(
        f"#️⃣ **Хэши**\n\n"
        f"MD5:    `{hashlib.md5(text).hexdigest()}`\n"
        f"SHA1:   `{hashlib.sha1(text).hexdigest()}`\n"
        f"SHA256: `{hashlib.sha256(text).hexdigest()}`\n"
        f"SHA512: `{hashlib.sha512(text).hexdigest()[:64]}…`"
    )

@client.on(events.NewMessage(pattern=r'/morse (.+)', from_users='me'))
async def morse_cmd(e):
    text = e.pattern_match.group(1).strip()
    await e.edit(f"📡 **Морзе:**\n_{text}_\n\n`{morse_enc(text)}`")

@client.on(events.NewMessage(pattern=r'/caesar (encode|decode) (\d+) (.+)', from_users='me'))
async def caesar_cmd(e):
    mode, shift, text = e.pattern_match.group(1), int(e.pattern_match.group(2)), e.pattern_match.group(3)
    res = caesar(text, shift, dec=(mode=='decode'))
    await e.edit(f"{'🔒' if mode=='encode' else '🔓'} **Цезарь (сдвиг {shift}):**\n_{text}_\n\n`{res}`")

@client.on(events.NewMessage(pattern=r'/vigenere (encode|decode) (\S+) (.+)', from_users='me'))
async def vigenere_cmd(e):
    mode, key, text = e.pattern_match.group(1), e.pattern_match.group(2), e.pattern_match.group(3)
    res = vigenere(text, key, dec=(mode=='decode'))
    await e.edit(f"{'🔒' if mode=='encode' else '🔓'} **Виженер (ключ: {key}):**\n_{text}_\n\n`{res}`")

@client.on(events.NewMessage(pattern=r'/password(?:\s+(\d+))?(?:\s+(simple))?$', from_users='me'))
async def password_cmd(e):
    length = max(4, min(int(e.pattern_match.group(1) or 16), 128))
    sym = not e.pattern_match.group(2)
    pwd = gen_pwd(length, sym)
    s = "🔴 Слабый" if length < 8 else "🟡 Средний" if length < 12 else "🟢 Сильный" if length < 20 else "💎 Очень сильный"
    await e.edit(f"🔑 **Пароль ({length} симв.)**\n\n`{pwd}`\n\nСила: {s}\nСимволы: {'✅' if sym else '❌'}")

@client.on(events.NewMessage(pattern=r'/qr (.+)', from_users='me'))
async def qr_cmd(e):
    text = e.pattern_match.group(1).strip().replace(' ','+')
    await e.edit(
        f"📱 **QR-код**\n\n"
        f"🔗 [Открыть изображение](https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={text})"
    )

@client.on(events.NewMessage(pattern=r'/uuid$', from_users='me'))
async def uuid_cmd(e):
    ids = [str(uuid.uuid4()) for _ in range(5)]
    out = "\n".join(f"`{u}`" for u in ids)
    await e.edit(f"🆔 **Случайные UUID v4:**\n\n{out}")

@client.on(events.NewMessage(pattern=r'/color (#[0-9a-fA-F]{6}|\d+,\d+,\d+)', from_users='me'))
async def color_cmd(e):
    raw = e.pattern_match.group(1).strip()
    if raw.startswith('#'):
        h = raw.lstrip('#')
        r,g,b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
        hex_val = raw.upper()
    else:
        r,g,b = map(int,raw.split(','))
        hex_val = f"#{r:02X}{g:02X}{b:02X}"
    rf,gf,bf = r/255,g/255,b/255
    mx,mn = max(rf,gf,bf), min(rf,gf,bf)
    l = (mx+mn)/2
    s_val = 0 if mx==mn else (mx-mn)/(1-abs(2*l-1))
    if mx==mn: h_val=0
    elif mx==rf: h_val=60*((gf-bf)/(mx-mn)%6)
    elif mx==gf: h_val=60*((bf-rf)/(mx-mn)+2)
    else: h_val=60*((rf-gf)/(mx-mn)+4)
    await e.edit(
        f"🎨 **Цвет**\n\n"
        f"HEX: `{hex_val}`\n"
        f"RGB: `rgb({r}, {g}, {b})`\n"
        f"HSL: `hsl({h_val:.0f}°, {s_val*100:.0f}%, {l*100:.0f}%)`\n\n"
        f"🔗 [Превью](https://www.colorhexa.com/{hex_val.lstrip('#')})"
    )

@client.on(events.NewMessage(pattern=r'/ascii (.+)', from_users='me'))
async def ascii_cmd(e):
    text = e.pattern_match.group(1).strip()
    codes = ' '.join(str(ord(c)) for c in text)
    back = ''.join(chr(int(x)) for x in codes.split())
    await e.edit(f"🔢 **ASCII коды:**\n_{text}_\n\n`{codes}`\n\nОбратно: `{back}`")

# ════════════════════════════════════════════════════════════
# 5. УПРАВЛЕНИЕ СООБЩЕНИЯМИ
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/type(?:\s+(fast|slow|matrix|glitch))?\s+(.+)', from_users='me'))
async def type_cmd(e):
    mode = e.pattern_match.group(1) or 'normal'
    text = e.pattern_match.group(2).strip()

    if mode == 'fast':
        msg = await e.edit("▌")
        for i in range(0, len(text), 2):
            chunk = text[:i+2]
            await msg.edit(chunk + ("▌" if i+2 < len(text) else ""))
            await asyncio.sleep(0.04)
        await msg.edit(text)

    elif mode == 'slow':
        msg = await e.edit("▌")
        shown = ""
        for ch in text:
            shown += ch
            await msg.edit(shown + "▌")
            pause = 0.3 if ch in '.!?…' else 0.12 if ch in ',;:' else 0.07
            await asyncio.sleep(pause)
        await msg.edit(text)

    elif mode == 'matrix':
        CHARS = string.ascii_letters + string.digits + "@#%&"
        msg = await e.edit("▓" * len(text))
        for step in range(len(text)):
            parts = list(text[:step])
            for _ in range(len(text) - step):
                parts.append(random.choice(CHARS))
            await msg.edit(''.join(parts))
            await asyncio.sleep(0.07)
        await msg.edit(text)

    elif mode == 'glitch':
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

    else:
        msg = await e.edit("▌")
        shown = ""
        for i, ch in enumerate(text):
            shown += ch
            if i % 2 == 0 or i == len(text)-1:
                await msg.edit(shown + ("▌" if i < len(text)-1 else ""))
                await asyncio.sleep(0.05)
        await msg.edit(text)

@client.on(events.NewMessage(pattern=r'/echo (.+)', from_users='me'))
async def echo_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, e.pattern_match.group(1).strip())

@client.on(events.NewMessage(pattern=r'/say (.+)', from_users='me'))
async def say_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, e.pattern_match.group(1).strip())

@client.on(events.NewMessage(pattern=r'/bold (.+)', from_users='me'))
async def bold_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, f"**{e.pattern_match.group(1).strip()}**")

@client.on(events.NewMessage(pattern=r'/italic (.+)', from_users='me'))
async def italic_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, f"__{e.pattern_match.group(1).strip()}__")

@client.on(events.NewMessage(pattern=r'/mono (.+)', from_users='me'))
async def mono_cmd(e):
    await e.delete()
    await client.send_message(e.chat_id, f"`{e.pattern_match.group(1).strip()}`")

@client.on(events.NewMessage(pattern=r'/clean(?:\s+(\d+))?$', from_users='me'))
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

@client.on(events.NewMessage(pattern=r'/purge(?:\s+(\d+))?$', from_users='me'))
async def purge_cmd(e):
    limit = int(e.pattern_match.group(1) or 10)
    await e.delete()
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        await msg.delete()
        count += 1
        await asyncio.sleep(0.04)
    info = await client.send_message(e.chat_id, f"⚠️ Удалено **{count}** сообщений")
    await asyncio.sleep(3)
    await info.delete()

@client.on(events.NewMessage(pattern=r'/spam (\d+) (.+)', from_users='me'))
async def spam_cmd(e):
    count, text = int(e.pattern_match.group(1)), e.pattern_match.group(2).strip()
    await e.delete()
    for _ in range(count):
        await client.send_message(e.chat_id, text)
        await asyncio.sleep(0.35)

@client.on(events.NewMessage(pattern=r'/forward (-?\d+)', from_users='me'))
async def forward_cmd(e):
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на сообщение: `/forward [chat_id]`")
        return
    try:
        msg = await e.get_reply_message()
        await client.forward_messages(int(e.pattern_match.group(1)), msg)
        await e.edit(f"✅ Переслано в `{e.pattern_match.group(1)}`")
    except Exception as ex:
        await e.edit(f"❌ {ex}")

@client.on(events.NewMessage(pattern=r'/pin$', from_users='me'))
async def pin_cmd(e):
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на сообщение")
        return
    await (await e.get_reply_message()).pin(notify=False)
    await e.delete()

@client.on(events.NewMessage(pattern=r'/unpin$', from_users='me'))
async def unpin_cmd(e):
    if e.reply_to_msg_id:
        await (await e.get_reply_message()).unpin()
    else:
        await client.unpin_message(e.chat_id)
    await e.delete()

@client.on(events.NewMessage(pattern=r'/copyall (\d+) (-?\d+)', from_users='me'))
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
            await client.forward_messages(target, m)
            copied += 1
            await asyncio.sleep(0.4)
        except Exception:
            pass
    await e.edit(f"✅ Скопировано **{copied}/{count}** → `{target}`")

@client.on(events.NewMessage(pattern=r'/react (.+)', from_users='me'))
async def react_cmd(e):
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на сообщение: `/react 👍`")
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
        await e.edit(f"❌ Не удалось поставить реакцию: {ex}")

# ════════════════════════════════════════════════════════════
# 6. ЗАМЕТКИ И TODO
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/save (\S+) (.+)', from_users='me'))
async def save_cmd(e):
    k, v = e.pattern_match.group(1), e.pattern_match.group(2)
    d = load_json(SAVED_FILE, {})
    d[k] = v
    write_json(SAVED_FILE, d)
    await e.edit(f"✅ `{k}` = _{v}_")

@client.on(events.NewMessage(pattern=r'/get (\S+)', from_users='me'))
async def get_cmd(e):
    k = e.pattern_match.group(1)
    d = load_json(SAVED_FILE, {})
    v = d.get(k)
    await e.edit(f"📦 `{k}` = _{v}_" if v else f"❌ Ключ `{k}` не найден")

@client.on(events.NewMessage(pattern=r'/del (\S+)', from_users='me'))
async def del_cmd(e):
    k = e.pattern_match.group(1)
    d = load_json(SAVED_FILE, {})
    if k in d:
        del d[k]
        write_json(SAVED_FILE, d)
        await e.edit(f"🗑 Удалено: `{k}`")
    else:
        await e.edit(f"❌ `{k}` не найден")

@client.on(events.NewMessage(pattern=r'/list$', from_users='me'))
async def list_cmd(e):
    d = load_json(SAVED_FILE, {})
    if not d:
        await e.edit("📭 Нет данных")
        return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v)>40 else ''}_" for k,v in d.items())
    await e.edit(f"📦 **Сохранено ({len(d)}):**\n\n{items}")

@client.on(events.NewMessage(pattern=r'/note (\S+)(?: (.+))?', from_users='me'))
async def note_cmd(e):
    k = e.pattern_match.group(1)
    t = e.pattern_match.group(2) or ""
    if e.reply_to_msg_id:
        r = await e.get_reply_message()
        t = r.text or t
    if not t:
        await e.edit("ℹ️ `/note <название> <текст>` или ответом")
        return
    d = load_json(NOTES_FILE, {})
    d[k] = t
    write_json(NOTES_FILE, d)
    await e.edit(f"📝 Заметка сохранена: `{k}`")

@client.on(events.NewMessage(pattern=r'/getnote (\S+)', from_users='me'))
async def getnote_cmd(e):
    k = e.pattern_match.group(1)
    d = load_json(NOTES_FILE, {})
    if k in d:
        await e.edit(f"📝 **{k}:**\n\n{d[k]}")
    else:
        await e.edit(f"❌ Заметка `{k}` не найдена")

@client.on(events.NewMessage(pattern=r'/delnote (\S+)', from_users='me'))
async def delnote_cmd(e):
    k = e.pattern_match.group(1)
    d = load_json(NOTES_FILE, {})
    if k in d:
        del d[k]
        write_json(NOTES_FILE, d)
        await e.edit(f"🗑 Заметка удалена: `{k}`")
    else:
        await e.edit(f"❌ `{k}` не найдена")

@client.on(events.NewMessage(pattern=r'/notes$', from_users='me'))
async def notes_cmd(e):
    d = load_json(NOTES_FILE, {})
    if not d:
        await e.edit("📭 Нет заметок")
        return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v)>40 else ''}_" for k,v in d.items())
    await e.edit(f"📝 **Заметки ({len(d)}):**\n\n{items}")

# TODO
@client.on(events.NewMessage(pattern=r'/todo (.+)', from_users='me'))
async def todo_add_cmd(e):
    task = e.pattern_match.group(1).strip()
    todos = load_json(TODOS_FILE, [])
    todos.append({'text': task, 'done': False, 'id': int(time.time())})
    write_json(TODOS_FILE, todos)
    await e.edit(f"✅ Задача добавлена: _{task}_\n📋 Всего: {len(todos)}")

@client.on(events.NewMessage(pattern=r'/todos$', from_users='me'))
async def todos_cmd(e):
    todos = load_json(TODOS_FILE, [])
    if not todos:
        await e.edit("📭 Список задач пуст")
        return
    lines = []
    for i, t in enumerate(todos, 1):
        mark = "✅" if t['done'] else "⬜"
        lines.append(f"{mark} {i}. _{t['text']}_")
    done = sum(1 for t in todos if t['done'])
    await e.edit(f"📋 **Список задач** ({done}/{len(todos)} выполнено):\n\n" + "\n".join(lines))

@client.on(events.NewMessage(pattern=r'/done (\d+)', from_users='me'))
async def done_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = load_json(TODOS_FILE, [])
    if 0 <= idx < len(todos):
        todos[idx]['done'] = True
        write_json(TODOS_FILE, todos)
        await e.edit(f"✅ Выполнено: _{todos[idx]['text']}_")
    else:
        await e.edit(f"❌ Задача #{idx+1} не найдена")

@client.on(events.NewMessage(pattern=r'/undone (\d+)', from_users='me'))
async def undone_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = load_json(TODOS_FILE, [])
    if 0 <= idx < len(todos):
        todos[idx]['done'] = False
        write_json(TODOS_FILE, todos)
        await e.edit(f"⬜ Снята отметка: _{todos[idx]['text']}_")
    else:
        await e.edit(f"❌ Задача #{idx+1} не найдена")

@client.on(events.NewMessage(pattern=r'/deltodo (\d+)', from_users='me'))
async def deltodo_cmd(e):
    idx = int(e.pattern_match.group(1)) - 1
    todos = load_json(TODOS_FILE, [])
    if 0 <= idx < len(todos):
        removed = todos.pop(idx)
        write_json(TODOS_FILE, todos)
        await e.edit(f"🗑 Удалена задача: _{removed['text']}_")
    else:
        await e.edit(f"❌ Задача #{idx+1} не найдена")

# ════════════════════════════════════════════════════════════
# 7. AFK
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/afk(?:\s+(.+))?$', from_users='me'))
async def afk_cmd(e):
    reason = (e.pattern_match.group(1) or '').strip()
    state.set_afk(reason)
    r = f"\n📝 _{reason}_" if reason else ""
    await e.edit(f"😴 **AFK включён**{r}")

@client.on(events.NewMessage(pattern=r'/unafk$', from_users='me'))
async def unafk_cmd(e):
    dur = state.clear_afk()
    if dur is not None:
        await e.edit(f"☀️ **AFK выключен** | Отсутствовал: _{fmt_time(dur)}_")
    else:
        await e.edit("ℹ️ AFK не был включён")

# ════════════════════════════════════════════════════════════
# 8. ИНФОРМАЦИЯ О ЧАТЕ
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/chatinfo$', from_users='me'))
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
    if members:
        lines.append(f"👤 Участников: `{members}`")
    await e.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'/members$', from_users='me'))
async def members_cmd(e):
    try:
        p = await client.get_participants(e.chat_id)
        bots = sum(1 for x in p if x.bot)
        await e.edit(f"👥 **Участники**\n\nВсего: `{len(p)}`\n👤 Людей: `{len(p)-bots}`\n🤖 Ботов: `{bots}`")
    except Exception as ex:
        await e.edit(f"❌ {ex}")

@client.on(events.NewMessage(pattern=r'/admins$', from_users='me'))
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

@client.on(events.NewMessage(pattern=r'/top(?:\s+(\d+))?$', from_users='me'))
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
    top = sorted(cnt.items(), key=lambda x: x[1], reverse=True)[:10]
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lines = [f"🏆 **Топ активных** (из {limit} сообщ.):\n"]
    for i, (uid, c) in enumerate(top):
        lines.append(f"{medals[i]} {names.get(uid, uid)} — `{c}` сообщ.")
    await e.edit("\n".join(lines))

@client.on(events.NewMessage(pattern=r'/bots$', from_users='me'))
async def bots_cmd(e):
    try:
        bots = await client.get_participants(e.chat_id, filter=ChannelParticipantsBots())
        lines = [f"🤖 **Боты в чате ({len(bots)}):**\n"]
        for b in bots[:20]:
            lines.append(f"• @{b.username or b.id}")
        await e.edit("\n".join(lines))
    except Exception as ex:
        await e.edit(f"❌ {ex}")

# ════════════════════════════════════════════════════════════
# 9. СБРОС ДАННЫХ
# ════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r'/resetdata$', from_users='me'))
async def resetdata_cmd(e):
    files = [DATA_FILE, SAVED_FILE, NOTES_FILE, TODOS_FILE, STATS_FILE]
    for f in files:
        if os.path.exists(f):
            os.remove(f)
            logger.info(f"Удалён {f}")
    state.auto_reply_enabled = False
    state.auto_reply_text = '💫 Я автоответчик, хозяин скоро ответит! Спасибо за терпение 😘'
    state.ghost_mode = False
    state.afk_start_time = None
    state.afk_reason = ''
    state.save()
    await e.edit("🧹 **Все данные сброшены.**")

# ════════════════════════════════════════════════════════════
# 10. HELP – РАБОЧАЯ ВЕРСИЯ С BUTTON.SWITCH_INLINE
# ════════════════════════════════════════════════════════════

# Данные о категориях и командах
COMMANDS_LIST = {
    'основные': [
        '/sleep', '/wake', '/setreply', '/status', '/time', '/ping',
        '/id', '/info', '/restart', '/ghost', '/resetdata'
    ],
    'профиль': [
        '/me', '/avatar', '/name', '/lastname', '/bio', '/whois', '/username_check'
    ],
    'игры': [
        '/dice', '/dart', '/basket', '/football', '/bowling', '/casino',
        '/coin', '/rand', '/8ball', '/rps', '/slot', '/lucky', '/choose', '/quiz'
    ],
    'утилиты': [
        '/calc', '/remind', '/search', '/shorten', '/weather', '/translate',
        '/base64', '/hash', '/morse', '/caesar', '/vigenere', '/password',
        '/qr', '/uuid', '/color', '/ascii'
    ],
    'сообщения': [
        '/type', '/echo', '/say', '/bold', '/italic', '/mono',
        '/clean', '/purge', '/spam', '/forward', '/pin', '/unpin',
        '/copyall', '/react'
    ],
    'заметки': [
        '/save', '/get', '/del', '/list',
        '/note', '/getnote', '/delnote', '/notes',
        '/todo', '/todos', '/done', '/undone', '/deltodo'
    ],
    'afk': ['/afk', '/unafk'],
    'инфо': ['/chatinfo', '/members', '/admins', '/top', '/bots'],
}

EMOJI_MAP = {
    'основные': '⚙️', 'профиль': '👤', 'игры': '🎮', 'утилиты': '🛠',
    'сообщения': '✉️', 'заметки': '📦', 'afk': '😴', 'инфо': '📊',
}

HELP_CATS = {
    'основные': (
        "⚙️ **ОСНОВНЫЕ КОМАНДЫ**\n\n"
        "`/sleep` — включить автоответчик\n"
        "`/wake` — выключить автоответчик\n"
        "`/setreply [текст]` — задать текст автоответчика\n"
        "`/status` — полный статус бота\n"
        "`/time` — время и дата\n"
        "`/ping` — задержка соединения\n"
        "`/id` — ID чата / пользователя\n"
        "`/info` — информация о боте\n"
        "`/restart` — перезапуск\n"
        "`/ghost` — ghost-режим\n"
        "`/resetdata` — сброс всех данных ⚠️\n\n"
        "👇 Нажми на команду — она вставится в поле ввода:"
    ),
    'профиль': (
        "👤 **ПРОФИЛЬ**\n\n"
        "`/me` — свой профиль\n"
        "`/avatar` — своя/чужая аватарка\n"
        "`/name [имя]` — сменить имя\n"
        "`/lastname [фамилия]` — сменить фамилию\n"
        "`/bio [текст]` — обновить «о себе»\n"
        "`/whois @ник` — инфо о пользователе\n"
        "`/username_check @ник` — проверить username\n\n"
        "👇 Нажми на команду — она вставится в поле ввода:"
    ),
    'игры': (
        "🎮 **ИГРЫ И РАЗВЛЕЧЕНИЯ**\n\n"
        "`/dice` `/dart` `/basket` `/football` `/bowling` `/casino` — анимации TG\n"
        "`/coin` — монетка\n"
        "`/rand` — случайное число\n"
        "`/8ball [вопрос]` — магический шар\n"
        "`/rps [к/н/б]` — камень-ножницы-бумага\n"
        "`/slot` — слот-машина\n"
        "`/lucky` — индекс удачи\n"
        "`/choose [вар1 | вар2]` — случайный выбор\n"
        "`/quiz` — викторина\n\n"
        "👇 Нажми на команду — она вставится в поле ввода:"
    ),
    'утилиты': (
        "🛠 **УТИЛИТЫ**\n\n"
        "`/calc [выражение]` — калькулятор\n"
        "`/remind [сек] [текст]` — напоминание\n"
        "`/search [запрос]` — поисковики\n"
        "`/shorten [url]` — сократить ссылку\n"
        "`/weather [город]` — погода\n"
        "`/translate [текст]` — перевод\n"
        "`/base64 encode/decode [текст]`\n"
        "`/hash [текст]` — MD5/SHA хэши\n"
        "`/morse [текст]` — азбука Морзе\n"
        "`/caesar encode/decode [сдвиг] [текст]`\n"
        "`/vigenere encode/decode [ключ] [текст]`\n"
        "`/password [длина] [simple]`\n"
        "`/qr [текст]` — QR-код\n"
        "`/uuid` — UUID v4\n"
        "`/color [#HEX или R,G,B]`\n"
        "`/ascii [текст]`\n\n"
        "👇 Нажми на команду — она вставится в поле ввода:"
    ),
    'сообщения': (
        "✉️ **СООБЩЕНИЯ**\n\n"
        "`/type [fast/slow/matrix/glitch] [текст]`\n"
        "`/echo [текст]` / `/say [текст]`\n"
        "`/bold` `/italic` `/mono` [текст]\n"
        "`/clean [n]` — удалить свои N сообщений\n"
        "`/purge [n]` — удалить любые N сообщений\n"
        "`/spam [n] [текст]`\n"
        "`/forward [chat_id]`\n"
        "`/pin` / `/unpin`\n"
        "`/copyall [n] [chat_id]`\n"
        "`/react [эмодзи]`\n\n"
        "👇 Нажми на команду — она вставится в поле ввода:"
    ),
    'заметки': (
        "📦 **ЗАМЕТКИ И TODO**\n\n"
        "**Хранилище:** `/save` `/get` `/del` `/list`\n"
        "**Заметки:** `/note` `/getnote` `/delnote` `/notes`\n"
        "**TODO:** `/todo` `/todos` `/done` `/undone` `/deltodo`\n\n"
        "👇 Нажми на команду — она вставится в поле ввода:"
    ),
    'afk': (
        "😴 **AFK**\n\n"
        "`/afk [причина]` — включить AFK-режим\n"
        "`/unafk` — выключить с отчётом времени\n\n"
        "👇 Нажми на команду — она вставится в поле ввода:"
    ),
    'инфо': (
        "📊 **ИНФОРМАЦИЯ О ЧАТЕ**\n\n"
        "`/chatinfo` — информация о чате\n"
        "`/members` — количество участников\n"
        "`/admins` — список администраторов\n"
        "`/top [n]` — топ активных\n"
        "`/bots` — список ботов\n\n"
        "👇 Нажми на команду — она вставится в поле ввода:"
    ),
}

@client.on(events.NewMessage(pattern=r'/help(?:\s+(.+))?$', from_users='me'))
async def help_cmd(e):
    cat = (e.pattern_match.group(1) or '').strip().lower()

    if cat:
        if cat not in HELP_CATS:
            cats = '  |  '.join(f"`/help {c}`" for c in HELP_CATS)
            await e.edit(f"❌ Категория `{cat}` не найдена.\n\n{cats}")
            bump_stat('cmds')
            return

        text = HELP_CATS[cat]
        # Собираем кнопки для команд из этой категории
        buttons = [Button.switch_inline(cmd, query=cmd, same_peer=True) for cmd in COMMANDS_LIST[cat]]
        rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
        await e.edit(text, buttons=rows)
        bump_stat('cmds')
        return

    # Главное меню – кнопки с /help категория
    lines = ["📚 **UserBot Help**\nВыбери категорию:\n"]
    buttons = []
    for cat in HELP_CATS:
        emoji = EMOJI_MAP.get(cat, '•')
        label = f"{emoji} {cat.capitalize()}"
        cmd = f"/help {cat}"
        buttons.append(Button.switch_inline(label, query=cmd, same_peer=True))

    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    await e.edit("\n".join(lines), buttons=rows)
    bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'/commands$', from_users='me'))
async def commands_cmd(e):
    all_cmds = []
    for cmds in COMMANDS_LIST.values():
        all_cmds.extend(cmds)
    buttons = [Button.switch_inline(cmd, query=cmd, same_peer=True) for cmd in all_cmds]
    rows = [buttons[i:i+4] for i in range(0, len(buttons), 4)]
    total = len(all_cmds)
    await e.edit(f"📋 **Все команды** · {total} шт.\nНажми — вставится в поле ввода:", buttons=rows)
    bump_stat('cmds')

# ════════════════════════════════════════════════════════════
# 11. ВХОДЯЩИЕ СООБЩЕНИЯ (автоответчик + AFK)
# ════════════════════════════════════════════════════════════

reply_cooldown = defaultdict(float)

@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def incoming_handler(event):
    sender = await event.get_sender()
    if not sender or sender.bot:
        return
    uid = event.sender_id
    now = time.time()

    if state.afk_start_time and now - reply_cooldown.get(f'afk_{uid}', 0) > 60:
        dur = fmt_time(now - state.afk_start_time)
        reason_part = f"\n📝 _{state.afk_reason}_" if state.afk_reason else ""
        reply_cooldown[f'afk_{uid}'] = now
        await event.reply(f"😴 Хозяин AFK уже **{dur}**{reason_part}")

    if state.auto_reply_enabled and now - reply_cooldown.get(uid, 0) > 10:
        reply_cooldown[uid] = now
        await asyncio.sleep(1)
        await event.reply(state.auto_reply_text)

# ════════════════════════════════════════════════════════════
# ЗАПУСК
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🚀 Запуск UserBot (71 команда)...")
    Thread(target=run_web, daemon=True).start()
    client.start()
    print("✅ UserBot запущен!")
    client.run_until_disconnected()
