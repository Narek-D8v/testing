import asyncio
import base64
import datetime
import hashlib
import json
import logging
import math
import os
import random
import re
import string
import time
import uuid
from collections import defaultdict
from threading import Thread

import aiohttp
import requests
from flask import Flask
import yt_dlp

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, MessageNotModifiedError
from telethon.tl.functions.account import (
    GetAuthorizationsRequest, UpdateProfileRequest, UpdateStatusRequest,
)
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import (
    ChannelParticipantsAdmins, ChannelParticipantsBots, InputMediaDice,
    ReactionEmoji,
)

from rp_commands import (
    RP_COMMANDS, format_rp_action, get_all_categories,
    get_category_commands, get_rp_reply,
)
from storage import Storage


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_ID = os.environ.get('API_ID')
API_HASH = os.environ.get('API_HASH')
PORT = int(os.environ.get('PORT', 8080))
STRING_SESSION = os.environ.get('STRING_SESSION')
OWNER_ID = int(os.environ.get('OWNER_ID', '5457847440'))
HIBP_API_KEY = os.environ.get('HIBP_API_KEY', '')
MEDIA_DIR = os.environ.get('MEDIA_DIR', './media')

db = Storage()

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

def check_cover(event):
    if state.cover_enabled:
        text = event.raw_text or ''
        if not text.startswith('!cover') and not text.startswith('!status_reset'):
            return True
    return False

class BotState:
    def __init__(self):
        self._load()

    def _load(self):
        self.auto_reply_enabled = db.get_state('auto_reply_enabled', 'False') == 'True'
        self.auto_reply_text = db.get_state('auto_reply_text',
            '💫 Я автоответчик, хозяин скоро ответит! Спасибо за терпение 😘')
        self.ghost_mode = db.get_state('ghost_mode', 'False') == 'True'
        raw_afk = db.get_state('afk_start_time')
        self.afk_start_time = float(raw_afk) if raw_afk else None
        self.afk_reason = db.get_state('afk_reason', '')
        self.bot_start_time = float(db.get_state('bot_start_time', str(time.time())))

        self.cover_enabled = db.get_state('cover_enabled', 'False') == 'True'
        self.silent_enabled = db.get_state('silent_enabled', 'False') == 'True'
        self.shadow_enabled = db.get_state('shadow_enabled', 'False') == 'True'
        self.shadow_delay = int(db.get_state('shadow_delay', '5'))
        self.lock_enabled = db.get_state('lock_enabled', 'False') == 'True'
        self.mute_enabled = db.get_state('mute_enabled', 'False') == 'True'
        self.typing_enabled = db.get_state('typing_enabled', 'False') == 'True'
        self.autodel_enabled = db.get_state('autodel_enabled', 'False') == 'True'
        self.autodel_delay = int(db.get_state('autodel_delay', '10'))
        self.reply_delay = int(db.get_state('reply_delay', '0'))
        self.readreceipt_enabled = db.get_state('readreceipt_enabled', 'True') == 'True'
        raw_sudo = db.get_state('sudo_users', '')
        self.sudo_users = set(int(x) for x in raw_sudo.split(',') if x.strip())

    def _save(self):
        db.set_state('auto_reply_enabled', str(self.auto_reply_enabled))
        db.set_state('auto_reply_text', self.auto_reply_text)
        db.set_state('ghost_mode', str(self.ghost_mode))
        db.set_state('afk_start_time', str(self.afk_start_time) if self.afk_start_time else '')
        db.set_state('afk_reason', self.afk_reason)
        db.set_state('bot_start_time', str(self.bot_start_time))
        db.set_state('cover_enabled', str(self.cover_enabled))
        db.set_state('silent_enabled', str(self.silent_enabled))
        db.set_state('shadow_enabled', str(self.shadow_enabled))
        db.set_state('shadow_delay', str(self.shadow_delay))
        db.set_state('lock_enabled', str(self.lock_enabled))
        db.set_state('mute_enabled', str(self.mute_enabled))
        db.set_state('typing_enabled', str(self.typing_enabled))
        db.set_state('autodel_enabled', str(self.autodel_enabled))
        db.set_state('autodel_delay', str(self.autodel_delay))
        db.set_state('reply_delay', str(self.reply_delay))
        db.set_state('readreceipt_enabled', str(self.readreceipt_enabled))
        db.set_state('sudo_users', ','.join(str(x) for x in self.sudo_users))

    def toggle_auto_reply(self, state=None):
        if state is None:
            self.auto_reply_enabled = not self.auto_reply_enabled
        else:
            self.auto_reply_enabled = state
        self._save()

    def set_auto_reply_text(self, text):
        self.auto_reply_text = text
        self._save()

    def toggle_ghost(self, state=None):
        if state is None:
            self.ghost_mode = not self.ghost_mode
        else:
            self.ghost_mode = state
        self._save()

    def set_afk(self, reason=''):
        self.afk_start_time = time.time()
        self.afk_reason = reason
        self._save()

    def clear_afk(self):
        duration = None
        if self.afk_start_time:
            duration = time.time() - self.afk_start_time
        self.afk_start_time = None
        self.afk_reason = ''
        self._save()
        return duration

    @property
    def uptime(self):
        return fmt_time(time.time() - self.bot_start_time)

    def set_cover(self, value):
        self.cover_enabled = value
        self._save()

    def set_silent(self, value):
        self.silent_enabled = value
        self._save()

    def set_shadow(self, value, delay=5):
        self.shadow_enabled = value
        self.shadow_delay = delay
        self._save()

    def set_lock(self, value):
        self.lock_enabled = value
        self._save()

    def set_mute(self, value):
        self.mute_enabled = value
        self._save()

    def set_typing(self, value):
        self.typing_enabled = value
        self._save()

    def set_autodel(self, value, delay=10):
        self.autodel_enabled = value
        self.autodel_delay = delay
        self._save()

    def set_reply_delay(self, seconds):
        self.reply_delay = max(0, seconds)
        self._save()

    def set_readreceipt(self, value):
        self.readreceipt_enabled = value
        self._save()

    def add_sudo(self, uid):
        self.sudo_users.add(uid)
        self._save()

    def remove_sudo(self, uid):
        self.sudo_users.discard(uid)
        self._save()

    def clear_sudo(self):
        self.sudo_users.clear()
        self._save()

    def reset_stealth(self):
        self.cover_enabled = False
        self.silent_enabled = False
        self.shadow_enabled = False
        self.shadow_delay = 5
        self.lock_enabled = False
        self.mute_enabled = False
        self.typing_enabled = False
        self.autodel_enabled = False
        self.autodel_delay = 10
        self.reply_delay = 0
        self.readreceipt_enabled = True
        self._save()

state = BotState()
command_cooldown = defaultdict(float)
reply_cooldown = {}
MAX_COOLDOWN_ENTRIES = 500
_download_lock = asyncio.Lock()
_watch_task = None
_protect_task = None


def owner_filter(event):
    return event.sender_id == OWNER_ID or event.sender_id in state.sudo_users


async def respond(event, text, **kwargs):
    if event.sender_id == OWNER_ID:
        try:
            return await event.edit(text, **kwargs)
        except MessageNotModifiedError:
            return None
    return await event.reply(text, **kwargs)


def format_bytes(n):
    for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"

_YT_DL_OPTS = {
    'outtmpl': os.path.join(MEDIA_DIR, '%(id)s.%(ext)s'),
    'quiet': True,
    'no_warnings': True,
    'merge_output_format': 'mp4',
    'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'android_creator'], 'player_skip': ['webpage', 'configs']}},
}


async def _download_yt_video(url, quality=None):
    def _dl():
        opts = dict(_YT_DL_OPTS)
        if quality:
            opts['format'] = f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]'
        else:
            opts['format'] = 'bestvideo+bestaudio/best'
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info.get('requested_downloads'):
                    return info['requested_downloads'][0]['filepath']
                return ydl.prepare_filename(info)
        except yt_dlp.utils.DownloadError as ex:
            raise ValueError(f"YouTube: {ex}")

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _dl)


async def _download_yt_audio(url):
    def _dl():
        opts = dict(_YT_DL_OPTS)
        opts['format'] = 'bestaudio'
        opts.pop('merge_output_format', None)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info.get('requested_downloads'):
                    return info['requested_downloads'][0]['filepath']
                return ydl.prepare_filename(info)
        except yt_dlp.utils.DownloadError as ex:
            raise ValueError(f"YouTube: {ex}")

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _dl)


async def _download_instagram_video(url):
    shortcode_match = re.search(r'instagram\.com/(?:p|reel|tv)/([^/?]+)', url)
    if not shortcode_match:
        raise ValueError("Неверная ссылка Instagram")

    def _dl():
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            html = resp.text
        except requests.RequestException as ex:
            raise ValueError(f"Ошибка загрузки страницы Instagram: {ex}")

        video_url = None
        patterns = [
            r'<meta\s+property="og:video"\s+content="([^"]+)"',
            r'"video_url":"([^"]+)"',
            r'"video_download_url":"([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                video_url = match.group(1).replace('\\/', '/').replace('\\u002F', '/')
                break

        if not video_url:
            match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?});', html, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    for k, v in data.items():
                        if isinstance(v, dict):
                            if 'video_url' in v:
                                video_url = v['video_url']
                                break
                            for k2, v2 in v.items():
                                if isinstance(v2, dict) and 'video_url' in v2:
                                    video_url = v2['video_url']
                                    break
                            if video_url:
                                break
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

        if not video_url:
            raise ValueError("Не удалось найти видео на странице Instagram")

        try:
            vr = requests.get(video_url, headers=headers, timeout=60)
            vr.raise_for_status()
        except requests.RequestException as ex:
            raise ValueError(f"Ошибка загрузки видео: {ex}")

        try:
            path = os.path.join(MEDIA_DIR, f'instagram_{int(time.time())}.mp4')
            with open(path, 'wb') as f:
                f.write(vr.content)
            return path
        except OSError as ex:
            raise ValueError(f"Ошибка сохранения файла: {ex}")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _dl)


async def _download_tiktok_video(url):
    def _dl():
        sess = requests.Session()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
        }
        try:
            resp = sess.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            html = resp.text
        except requests.RequestException as ex:
            raise ValueError(f"Ошибка загрузки страницы TikTok: {ex}")

        video_url = None
        patterns = [
            r'"downloadAddr":"([^"]+)"',
            r'"playAddr":"([^"]+)"',
            r'"play":"([^"]+)"',
            r'<video[^>]+src="([^"]+)"',
            r'"url_list":\["([^"]+)"\]',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                video_url = match.group(1).replace('\\/', '/').replace('\\u002F', '/')
                if video_url.startswith('//'):
                    video_url = 'https:' + video_url
                break

        if not video_url:
            match = re.search(r'"video":{"videoUrl":{"urlList":\["([^"]+)"', html)
            if match:
                video_url = match.group(1).replace('\\/', '/').replace('\\u002F', '/')

        if not video_url:
            raise ValueError("Не удалось найти видео на странице TikTok")

        dl_headers = {
            'User-Agent': headers['User-Agent'],
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': '*/*',
            'Referer': url,
            'Origin': 'https://www.tiktok.com',
            'Sec-Fetch-Dest': 'video',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'same-site',
        }
        try:
            vr = sess.get(video_url, headers=dl_headers, timeout=60)
            vr.raise_for_status()
        except requests.RequestException as ex:
            raise ValueError(f"Ошибка загрузки видео TikTok: {ex}")

        try:
            path = os.path.join(MEDIA_DIR, f'tiktok_{int(time.time())}.mp4')
            with open(path, 'wb') as f:
                f.write(vr.content)
            return path
        except OSError as ex:
            raise ValueError(f"Ошибка сохранения файла: {ex}")

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _dl)


async def _run_download(event_edit_func, url, mode='video', quality=None, timeout=600):
    if _download_lock.locked():
        await event_edit_func("⏳ Уже идёт загрузка, встаю в очередь...")
    async with _download_lock:
        logger.info(f"[download] Starting: {url} (mode={mode})")
        try:
            is_instagram = 'instagram.com' in url.lower()
            is_youtube = 'youtube.com' in url.lower() or 'youtu.be' in url.lower()
            is_tiktok = 'tiktok.com' in url.lower()

            if is_tiktok:
                await event_edit_func("📥 Скачиваю из TikTok...")
                filename = await asyncio.wait_for(
                    _download_tiktok_video(url), timeout=timeout
                )
            elif is_instagram:
                await event_edit_func("📥 Скачиваю из Instagram...")
                filename = await asyncio.wait_for(
                    _download_instagram_video(url), timeout=timeout
                )
            elif is_youtube:
                if mode == 'audio':
                    await event_edit_func("🎵 Скачиваю аудио...")
                    filename = await asyncio.wait_for(
                        _download_yt_audio(url), timeout=timeout
                    )
                else:
                    qual = f"{quality}p" if quality else None
                    await event_edit_func(f"📥 Скачиваю видео ({qual or 'авто'})...")
                    filename = await asyncio.wait_for(
                        _download_yt_video(url, quality), timeout=timeout
                    )
            else:
                await event_edit_func("❌ Поддерживаются: YouTube, Instagram, TikTok.")
                return None

            if filename and os.path.exists(filename):
                size = format_bytes(os.path.getsize(filename))
                logger.info(f"[download] OK: {filename} ({size})")
                return filename
            logger.warning(f"[download] file not found after download: {url}")
            return None
        except asyncio.TimeoutError:
            await event_edit_func("❌ Превышено время ожидания (10 мин).")
            logger.warning(f"Download timeout: {url}")
            return None
        except ValueError as ex:
            logger.warning(f"Download error: {ex}")
            await event_edit_func(f"❌ {ex}")
            return None
        except Exception as ex:
            logger.error(f"Download error: {ex}", exc_info=True)
            await event_edit_func(f"❌ **Ошибка:** {ex}")
            return None


async def _send_and_clean(event_edit_func, chat_id, filepath, caption=''):
    if not filepath or not os.path.exists(filepath):
        return
    try:
        size_mb = os.path.getsize(filepath) / 1024 / 1024
    except OSError:
        return
    if size_mb > 1500:
        await event_edit_func("❌ Слишком большой файл (>1.5 ГБ).")
    else:
        await event_edit_func("📤 **Отправляю файл...**")
        try:
            await client.send_file(chat_id, filepath, caption=caption)
        except FloodWaitError as fwe:
            logger.warning(f"FloodWait при отправке: {fwe.seconds}s")
            await asyncio.sleep(fwe.seconds + 1)
            try:
                await client.send_file(chat_id, filepath, caption=caption)
            except Exception as ex2:
                await event_edit_func(f"❌ Ошибка отправки: {ex2}")
        except Exception as ex:
            await event_edit_func(f"❌ Ошибка отправки: {ex}")
    await asyncio.sleep(5)
    for _ in range(3):
        try:
            os.remove(filepath)
            break
        except OSError:
            await asyncio.sleep(1)

def create_client():
    if STRING_SESSION:
        from telethon.sessions import StringSession
        return TelegramClient(StringSession(STRING_SESSION), int(API_ID), API_HASH)
    return TelegramClient('my_userbot', int(API_ID), API_HASH)

client = create_client()

app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 UserBot работает 24/7!"

def run_web():
    app.run(host='0.0.0.0', port=PORT)

@client.on(events.NewMessage(pattern=r'!sleep$', func=owner_filter))
async def sleep_cmd(e):
    if check_cover(e): return
    state.toggle_auto_reply(True)
    await respond(e, '💤 Автоответчик **ВКЛЮЧЕН**.')
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!wake$', func=owner_filter))
async def wake_cmd(e):
    if check_cover(e): return
    state.toggle_auto_reply(False)
    await respond(e, '☀️ Автоответчик **ВЫКЛЮЧЕН**.')
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!setreply(?:\s+(@\w+))?(?:\s+(.+))?', func=owner_filter))
async def setreply_cmd(e):
    if check_cover(e): return
    g = e.pattern_match
    target, text = g.group(1), g.group(2)
    if target and target.lower() == '@default':
        db.set_default_reply(text or '')
        await respond(e, f"✅ Дефолтный ответ установлен:\n_{text or 'пусто'}_")
    elif target:
        db.set_reply_text(target.lstrip('@'), text or '')
        await respond(e, f"✅ Ответ для {target} установлен:\n_{text or 'пусто'}_")
    elif text:
        state.set_auto_reply_text(text)
        await respond(e, f"✅ Текст автоответчика:\n_{text}_")
    else:
        await respond(e, "ℹ️ `!setreply @username текст` или `!setreply default текст`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!status$', func=owner_filter))
async def status_cmd(e):
    if check_cover(e): return
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    s = db.all_stats()
    afk_status = f"✅ {state.afk_reason or 'без причины'}" if state.afk_start_time else "❌"
    await respond(e, 
        f"📊 **Статус UserBot**\n\n"
        f"👤 {me.first_name} {me.last_name or ''}\n"
        f"💬 Чатов: `{len(dialogs)}`\n"
        f"🤖 Автоответчик: {'💤 Вкл' if state.auto_reply_enabled else '☀️ Выкл'}\n"
        f"👻 Ghost: {'✅' if state.ghost_mode else '❌'}\n"
        f"🔇 Cover: {'✅' if state.cover_enabled else '❌'}\n"
        f"🤐 Silent: {'✅' if state.silent_enabled else '❌'}\n"
        f"👤 Shadow: {'✅' if state.shadow_enabled else '❌'}\n"
        f"🔒 Lock: {'✅' if state.lock_enabled else '❌'}\n"
        f"🔇 Mute: {'✅' if state.mute_enabled else '❌'}\n"
        f"⌨️ Тайпинг: {'✅' if state.typing_enabled else '❌'}\n"
        f"🗑️ Автоудал: {'✅' if state.autodel_enabled else '❌'}\n"
        f"⏳ Задержка: `{state.reply_delay}с`\n"
        f"👁️ Прочтение: {'✅' if state.readreceipt_enabled else '❌'}\n"
        f"😴 AFK: {afk_status}\n"
        f"👑 Sudo: `{len(state.sudo_users)}`\n"
        f"⏱ Аптайм: `{state.uptime}`\n"
        f"📨 Команд выполнено: `{s.get('cmds', 0)}`"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!time$', func=owner_filter))
async def time_cmd(e):
    if check_cover(e): return
    now = datetime.datetime.now()
    utc = datetime.datetime.utcnow()
    week_days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
    await respond(e, 
        f"🕐 **Время и дата**\n\n"
        f"🏠 Локальное: `{now.strftime('%H:%M:%S')}`\n"
        f"🌍 UTC: `{utc.strftime('%H:%M:%S')}`\n"
        f"📅 Дата: `{now.strftime('%d.%m.%Y')}`\n"
        f"📆 День: **{week_days[now.weekday()]}**"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!ping$', func=owner_filter))
async def ping_cmd(e):
    if check_cover(e): return
    t0 = time.monotonic()
    await respond(e, "🏓 ...")
    ms = (time.monotonic() - t0) * 1000
    q = "🟢 Отлично" if ms < 150 else "🟡 Нормально" if ms < 400 else "🔴 Высокая"
    await respond(e, f"🏓 **Понг!**\n⚡ Задержка: `{ms:.1f} мс`\n📶 Качество: {q}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!id$', func=owner_filter))
async def id_cmd(e):
    if check_cover(e): return
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
    await respond(e, "\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!info$', func=owner_filter))
async def info_cmd(e):
    if check_cover(e): return
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    await respond(e, 
        f"🚀 **UserBot Info**\n\n"
        f"👤 {me.first_name} {me.last_name or ''}\n"
        f"🆔 ID: `{me.id}`\n"
        f"🔰 @{me.username or 'нет'}\n"
        f"📱 Телефон: `{me.phone or 'скрыт'}`\n"
        f"💬 Чатов: `{len(dialogs)}`\n"
        f"⏱ Аптайм: `{state.uptime}`\n"
        f"⚡ Статус: **Активен** ✅"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!restart$', func=owner_filter))
async def restart_cmd(e):
    if check_cover(e): return
    await respond(e, '🔄 Перезагрузка...')
    await asyncio.sleep(2)
    await client.disconnect()
    os._exit(0)

@client.on(events.NewMessage(pattern=r'!ghost$', func=owner_filter))
async def ghost_cmd(e):
    if check_cover(e): return
    state.toggle_ghost()
    if state.ghost_mode:
        await respond(e, "👻 **Ghost-режим ВКЛЮЧЁН** — команды удаляются мгновенно")
        await asyncio.sleep(2)
        await e.delete()
    else:
        await respond(e, "👁 **Ghost-режим ВЫКЛЮЧЕН**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!cover(?:\s+(off|on))?$', func=owner_filter))
async def cover_cmd(e):
    arg = e.pattern_match.group(1)
    if arg == 'off':
        state.set_cover(False)
        await respond(e, "🛡️ **Cover-режим ВЫКЛЮЧЕН** — команды снова работают.")
    else:
        state.set_cover(True)
        await respond(e, "🛡️ **Cover-режим ВКЛЮЧЁН** — все команды, кроме `!cover off`, игнорируются.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!silent\s*(on|off)?$', func=owner_filter))
async def silent_cmd(e):
    arg = e.pattern_match.group(1)
    if arg == 'off':
        state.set_silent(False)
        await respond(e, "🔇 **Silent-режим ВЫКЛЮЧЕН** — ответы снова отправляются.")
    else:
        state.set_silent(True)
        await respond(e, "🔇 **Silent-режим ВКЛЮЧЁН** — бот молчит в ЛС.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!shadow(?:\s+(\d+))?$', func=owner_filter))
async def shadow_cmd(e):
    delay = e.pattern_match.group(1)
    if delay:
        d = int(delay)
        state.set_shadow(True, max(1, d))
        await respond(e, f"👤 **Shadow-режим ВКЛЮЧЁН** — удаление через {max(1, d)} сек.")
    elif state.shadow_enabled:
        state.set_shadow(False)
        await respond(e, "👤 **Shadow-режим ВЫКЛЮЧЕН** — автодудаление отключено.")
    else:
        state.set_shadow(True)
        await respond(e, "👤 **Shadow-режим ВКЛЮЧЁН** — удаление через 5 сек.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!lock(?:\s+(on|off))?$', func=owner_filter))
async def lock_cmd(e):
    arg = e.pattern_match.group(1)
    if arg == 'off':
        state.set_lock(False)
        await respond(e, "🔒 **Lock-режим ВЫКЛЮЧЕН** — ЛС от всех открыты.")
    else:
        state.set_lock(True)
        await respond(e, "🔒 **Lock-режим ВКЛЮЧЁН** — бот отвечает только контактам.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!mute(?:\s+(on|off))?$', func=owner_filter))
async def mute_cmd(e):
    arg = e.pattern_match.group(1)
    if arg == 'off':
        state.set_mute(False)
        await respond(e, "🔇 **Mute-режим ВЫКЛЮЧЕН** — ЛС принимаются.")
    else:
        state.set_mute(True)
        await respond(e, "🔇 **Mute-режим ВКЛЮЧЁН** — все ЛС игнорируются.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!typing(?:\s+(on|off))?$', func=owner_filter))
async def typing_cmd(e):
    if check_cover(e): return
    arg = e.pattern_match.group(1)
    if arg == 'off':
        state.set_typing(False)
        await respond(e, "⌨️ **Тайпинг ВЫКЛЮЧЕН** — индикатор печати не показывается.")
    else:
        state.set_typing(True)
        await respond(e, "⌨️ **Тайпинг ВКЛЮЧЁН** — перед ответом показывается «печатает...».")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!autodel(?:\s+(on|off))?(?:\s+(\d+))?$', func=owner_filter))
async def autodel_cmd(e):
    if check_cover(e): return
    arg = e.pattern_match.group(1)
    delay_str = e.pattern_match.group(2)
    if arg == 'off':
        state.set_autodel(False)
        await respond(e, "🗑️ **Автоудаление ВЫКЛЮЧЕНО** — сообщения не удаляются.")
    else:
        d = int(delay_str) if delay_str else 10
        state.set_autodel(True, max(3, d))
        await respond(e, f"🗑️ **Автоудаление ВКЛЮЧЕНО** — удаление через {max(3, d)} сек.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!delay\s+(\d+)', func=owner_filter))
async def delay_cmd(e):
    if check_cover(e): return
    sec = int(e.pattern_match.group(1))
    state.set_reply_delay(min(sec, 30))
    if state.reply_delay > 0:
        await respond(e, f"⏳ **Задержка ответа: {state.reply_delay} сек.**")
    else:
        await respond(e, "⏳ **Задержка ответа ВЫКЛЮЧЕНА.**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!readreceipt(?:\s+(on|off))?$', func=owner_filter))
async def readreceipt_cmd(e):
    if check_cover(e): return
    arg = e.pattern_match.group(1)
    if arg == 'off':
        state.set_readreceipt(False)
        await respond(e, "👁️ **Прочтение ВЫКЛЮЧЕНО** — сообщения остаются непрочитанными.")
    else:
        state.set_readreceipt(True)
        await respond(e, "👁️ **Прочтение ВКЛЮЧЕНО** — сообщения отмечаются прочитанными.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!sudo(?:\s+(on|off)\s+(\S+))?\s*$', func=owner_filter))
async def sudo_cmd(e):
    if check_cover(e): return
    g = e.pattern_match
    action = g.group(1)
    target = g.group(2)
    if not action:
        if state.sudo_users:
            lines = ["👑 **Sudo-пользователи:**\n"]
            for uid in list(state.sudo_users):
                try:
                    ent = await client.get_entity(uid)
                    name = getattr(ent, 'first_name', '') or str(uid)
                    uname = f" @{ent.username}" if getattr(ent, 'username', None) else ''
                    lines.append(f"• {name}{uname} (`{uid}`)")
                except Exception:
                    lines.append(f"• `{uid}`")
            await respond(e, "\n".join(lines))
        else:
            await respond(e, "👑 **Sudo-пользователи отсутствуют.**")
        db.bump_stat('cmds')
        return
    try:
        ent = await client.get_entity(target)
    except Exception as ex:
        await respond(e, f"❌ Пользователь {target} не найден: {ex}")
        db.bump_stat('cmds')
        return
    name = getattr(ent, 'first_name', '') or str(ent.id)
    if action == 'on':
        state.add_sudo(ent.id)
        await respond(e, f"👑 **{name}** добавлен в sudo.")
    else:
        state.remove_sudo(ent.id)
        await respond(e, f"👑 **{name}** удалён из sudo.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!online$', func=owner_filter))
async def online_cmd(e):
    if check_cover(e): return
    try:
        await client(UpdateStatusRequest(offline=False))
        await respond(e, "🟢 Статус: **Онлайн**")
    except Exception as ex:
        await respond(e, f"❌ Ошибка: {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!offline$', func=owner_filter))
async def offline_cmd(e):
    if check_cover(e): return
    try:
        await client(UpdateStatusRequest(offline=True))
        await respond(e, "🔴 Статус: **Недавно был(а)**")
    except Exception as ex:
        await respond(e, f"❌ Ошибка: {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!status_reset$', func=owner_filter))
async def status_reset_cmd(e):
    state.reset_stealth()
    await respond(e, "🔄 **Все стелс-режимы сброшены**: cover, silent, shadow, lock, mute — выключены.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!me$', func=owner_filter))
async def me_cmd(e):
    if check_cover(e): return
    me = await client.get_me()
    photos = await client.get_profile_photos(me.id, limit=1)
    await respond(e, 
        f"👤 **Мой профиль**\n\n"
        f"📛 {me.first_name} {me.last_name or ''}\n"
        f"🆔 `{me.id}`\n"
        f"🔰 @{me.username or 'нет'}\n"
        f"📱 `{me.phone or 'скрыт'}`\n"
        f"🖼 Аватар: {'✅' if photos else '❌'}\n"
        f"✔️ Verified: {'✅' if me.verified else '❌'}\n"
        f"🤖 Бот: {'✅' if me.bot else '❌'}"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!avatar$', func=owner_filter))
async def avatar_cmd(e):
    if check_cover(e): return
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
        await respond(e, "❌ Аватарка не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!name (.+)', func=owner_filter))
async def name_cmd(e):
    if check_cover(e): return
    n = e.pattern_match.group(1).strip()
    await client.edit_profile(first_name=n)
    await respond(e, f"✅ Имя → **{n}**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!lastname(?:\s+(.+))?$', func=owner_filter))
async def lastname_cmd(e):
    if check_cover(e): return
    n = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(last_name=n)
    await respond(e, f"✅ Фамилия → **{n}**" if n else "✅ Фамилия удалена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!bio(?:\s+(.+))?$', func=owner_filter))
async def bio_cmd(e):
    if check_cover(e): return
    t = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(about=t)
    await respond(e, f"✅ Био → _{t}_" if t else "✅ Био очищено")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!whois (.+)', func=owner_filter))
async def whois_cmd(e):
    if check_cover(e): return
    target = e.pattern_match.group(1).strip().lstrip('@')
    try:
        ent = await client.get_entity(target)
        name = f"{getattr(ent, 'first_name', '') or ''} {getattr(ent, 'last_name', '') or ''}".strip() \
               or getattr(ent, 'title', '?')
        uname = f"@{ent.username}" if getattr(ent, 'username', None) else "нет"
        bot_ = "✅" if getattr(ent, 'bot', False) else "❌"
        ver = "✅" if getattr(ent, 'verified', False) else "❌"
        await respond(e, 
            f"🔍 **Информация о пользователе**\n\n"
            f"📛 Имя: **{name}**\n"
            f"🆔 ID: `{ent.id}`\n"
            f"🔰 Username: {uname}\n"
            f"🤖 Бот: {bot_}\n"
            f"✔️ Verified: {ver}"
        )
    except Exception as ex:
        await respond(e, f"❌ Не найден: {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!username_check (.+)', func=owner_filter))
async def username_check_cmd(e):
    if check_cover(e): return
    uname = e.pattern_match.group(1).strip().lstrip('@')
    try:
        ent = await client.get_entity(uname)
        name = getattr(ent, 'first_name', None) or getattr(ent, 'title', '?')
        await respond(e, f"🔍 @{uname}\n✅ **Занят**\n👤 {name}\n🆔 `{ent.id}`")
    except Exception:
        await respond(e, f"🔍 @{uname}\n✅ **Свободен**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!dice$', func=owner_filter))
async def dice_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎲'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!dart$', func=owner_filter))
async def dart_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎯'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!basket$', func=owner_filter))
async def basket_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🏀'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!football$', func=owner_filter))
async def football_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('⚽'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!bowling$', func=owner_filter))
async def bowling_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎳'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!casino$', func=owner_filter))
async def casino_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎰'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!coin$', func=owner_filter))
async def coin_cmd(e):
    if check_cover(e): return
    sides = ["Орёл 🦅", "Решка 💰"]
    r = random.choice(sides)
    flips = random.randint(3, 9)
    await respond(e, f"🪙 Монета вращается {flips} раз...\n\nРезультат: **{r}**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!rand(?:\s+(-?\d+)(?:\s+(-?\d+))?)?$', func=owner_filter))
async def rand_cmd(e):
    if check_cover(e): return
    g = e.pattern_match
    a, b = g.group(1), g.group(2)
    if a and b:
        lo, hi = sorted([int(a), int(b)])
        await respond(e, f"🎲 `{lo}` … `{hi}` → **{random.randint(lo, hi)}**")
    elif a:
        await respond(e, f"🎲 `1` … `{a}` → **{random.randint(1, int(a))}**")
    else:
        await respond(e, f"🎲 **{random.randint(1, 100)}**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!8ball(?:\s+(.+))?$', func=owner_filter))
async def eightball_cmd(e):
    if check_cover(e): return
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
    msg = await respond(e, "🎱 Шар вращается...")
    for frame in spin:
        await msg.edit(f"{frame} Шар вращается...")
        await asyncio.sleep(0.15)
    pool_key = random.choices(['pos', 'neu', 'neg'], weights=[38, 27, 35])[0]
    answer, emoji, comment = random.choice(ANSWERS[pool_key])
    color = {"pos": "🟢", "neu": "🟡", "neg": "🔴"}[pool_key]
    label = {"pos": "ПОЗИТИВНЫЙ", "neu": "НЕЙТРАЛЬНЫЙ", "neg": "НЕГАТИВНЫЙ"}[pool_key]
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
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!rps(?:\s+(.+))?$', func=owner_filter))
async def rps_cmd(e):
    if check_cover(e): return
    MAP = {'к': '🪨 Камень', 'камень': '🪨 Камень', 'н': '✂️ Ножницы', 'ножницы': '✂️ Ножницы', 'б': '📄 Бумага', 'бумага': '📄 Бумага'}
    BOT = ['🪨 Камень', '✂️ Ножницы', '📄 Бумага']
    WIN = {'🪨 Камень': '✂️ Ножницы', '✂️ Ножницы': '📄 Бумага', '📄 Бумага': '🪨 Камень'}
    arg = (e.pattern_match.group(1) or '').lower().strip()
    if not arg or arg not in MAP:
        await respond(e, "✊✌️🖐 `!rps камень` / `ножницы` / `бумага` (или `к`!`н`!`б`)")
        return
    uc, bc = MAP[arg], random.choice(BOT)
    if uc == bc:
        res = "🤝 **Ничья!**"
    elif WIN[uc] == bc:
        res = "🏆 **Ты победил!**"
    else:
        res = "💀 **Бот победил!**"
    await respond(e, f"✊✌️🖐 **КНБ**\n\n👤 Ты: {uc}\n🤖 Бот: {bc}\n\n{res}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!slot$', func=owner_filter))
async def slot_cmd(e):
    if check_cover(e): return
    SYM = ['🍒', '🍋', '🍊', '🍇', '🍉', '⭐', '💎', '7️⃣', '🔔', '🍀']
    msg = await respond(e, "🎰 [ ▓ | ▓ | ▓ ]")
    for _ in range(4):
        s = [random.choice(SYM) for _ in range(3)]
        await msg.edit(f"🎰 [ {s[0]} | {s[1]} | {s[2]} ]")
        await asyncio.sleep(0.3)
    s = [random.choice(SYM) for _ in range(3)]
    if s[0] == s[1] == s[2]:
        res = "💰💰💰 **ДЖЕКПОТ!**" if s[0] in ('💎', '7️⃣') else "🎊 **Выигрыш! Три одинаковых!**"
    elif len(set(s)) < 3:
        res = "😅 Почти! Два одинаковых — ещё раз!"
    else:
        res = "💸 Не повезло. Попробуй снова!"
    await msg.edit(f"🎰 [ {s[0]} | {s[1]} | {s[2]} ]\n\n{res}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!lucky$', func=owner_filter))
async def lucky_cmd(e):
    if check_cover(e): return
    pct = random.randint(0, 100)
    bar = progress_bar(pct, 100, 12)
    tips = {
        (90, 100): "🌟 АБСОЛЮТНАЯ УДАЧА! Сегодня твой день!",
        (70, 89): "🍀 Очень удачный день — действуй!",
        (50, 69): "😊 Неплохо — удача на твоей стороне",
        (30, 49): "😐 Средний день, будь осторожен",
        (10, 29): "😬 Не лучший день...",
        (0, 9): "💀 Сиди дома и не высовывайся!",
    }
    msg = next(v for (a, b), v in tips.items() if a <= pct <= b)
    await respond(e, f"🔮 **Индекс удачи**\n\n[{bar}] **{pct}%**\n\n{msg}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!choose (.+)', func=owner_filter))
async def choose_cmd(e):
    if check_cover(e): return
    raw = e.pattern_match.group(1)
    opts = [o.strip() for o in re.split(r'[,|/]', raw) if o.strip()]
    if len(opts) < 2:
        await respond(e, "ℹ️ Перечисли варианты через запятую: `!choose пицца, суши, бургер`")
        return
    winner = random.choice(opts)
    listed = "\n".join(f"{'➡️' if o == winner else '  •'} {o}" for o in opts)
    await respond(e, f"🤔 **Выбираю из {len(opts)} вариантов...**\n\n{listed}\n\n✅ **Выбор: {winner}**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!quiz$', func=owner_filter))
async def quiz_cmd(e):
    if check_cover(e): return
    QUESTIONS = [
        ("Столица Австралии?", ["Сидней", "Мельбурн", "Канберра", "Перт"], 2),
        ("Сколько планет в Солнечной системе?", ["7", "8", "9", "10"], 1),
        ("Кто написал «Гамлета»?", ["Диккенс", "Толстой", "Шекспир", "Гёте"], 2),
        ("Химический символ золота?", ["Go", "Gd", "Au", "Ag"], 2),
        ("Год основания Google?", ["1996", "1998", "2000", "2002"], 1),
        ("Самая длинная река мира?", ["Амазонка", "Янцзы", "Нил", "Конго"], 2),
        ("Сколько байт в килобайте?", ["512", "1024", "2048", "4096"], 1),
        ("Скорость света (км/с)?", ["150 000", "300 000", "450 000", "600 000"], 1),
    ]
    q, opts, ans_idx = random.choice(QUESTIONS)
    letters = ['A', 'B', 'C', 'D']
    opts_text = "\n".join(f"{letters[i]}. {o}" for i, o in enumerate(opts))
    correct = f"{letters[ans_idx]}. {opts[ans_idx]}"
    await respond(e, 
        f"🧠 **Вопрос:**\n_{q}_\n\n{opts_text}\n\n"
        f"||✅ Ответ: **{correct}**||"
    )
    db.bump_stat('cmds')

async def safe_eval(expr: str):
    func_map = {
        'sqrt': 'math.sqrt', 'sin': 'math.sin', 'cos': 'math.cos', 'tan': 'math.tan',
        'log': 'math.log', 'log2': 'math.log2', 'log10': 'math.log10',
        'abs': 'abs', 'pow': 'pow', 'floor': 'math.floor', 'ceil': 'math.ceil',
        'round': 'round', 'pi': 'math.pi', 'e': 'math.e',
        'factorial': 'math.factorial', 'gcd': 'math.gcd', 'hypot': 'math.hypot',
    }
    safe = expr.strip()
    for k, v in func_map.items():
        safe = re.sub(rf'\b{k}\b', v, safe)
    if any(w in safe for w in ['import', 'os', 'sys', 'open', 'exec', 'eval', '__']):
        return None
    ns = {'__builtins__': {}, 'math': math, 'abs': abs, 'pow': pow, 'round': round}
    try:
        r = eval(safe, ns, {})
        if isinstance(r, float):
            if math.isinf(r) or math.isnan(r):
                return "∞"
            return round(r, 10)
        return r
    except Exception:
        return None

def caesar(text, shift, dec=False):
    if dec:
        shift = -shift
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
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.',
    'G': '--.', 'H': '....', 'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..',
    'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
    'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-',
    'Y': '-.--', 'Z': '--..',
    '0': '-----', '1': '.----', '2': '..---', '3': '...--', '4': '....-',
    '5': '.....', '6': '-....', '7': '--...', '8': '---..', '9': '----.',
    ' ': '/'
}

def morse_enc(t):
    return ' '.join(_MORSE.get(c.upper(), '?') for c in t)

def gen_pwd(n=16, sym=True):
    pool = string.ascii_letters + string.digits + ("!@#$%^&*()-_=+[]{}|;:,.<>?" if sym else "")
    return ''.join(random.SystemRandom().choice(pool) for _ in range(n))

def vigenere(text, key, dec=False):
    key = key.upper()
    out, ki = [], 0
    for c in text:
        if c.isalpha():
            shift = ord(key[ki % len(key)]) - ord('A')
            if dec:
                shift = -shift
            base = ord('A' if c.isupper() else 'a')
            out.append(chr((ord(c) - base + shift) % 26 + base))
            ki += 1
        else:
            out.append(c)
    return ''.join(out)

@client.on(events.NewMessage(pattern=r'!calc (.+)', func=owner_filter))
async def calc_cmd(e):
    if check_cover(e): return
    expr = e.pattern_match.group(1).strip()
    r = await safe_eval(expr)
    if r is not None:
        await respond(e, f"🧮 `{expr}` = **{r}**")
    else:
        await respond(e, "❌ Ошибка выражения. Разрешены: `+ - * / % sqrt sin cos tan log abs pow pi e factorial ceil floor round`")
    db.bump_stat('cmds')

async def send_reminder(chat_id, msg_text, delay):
    await asyncio.sleep(delay)
    try:
        await client.send_message(chat_id, f"⏰ **НАПОМИНАНИЕ:**\n{msg_text}")
    except Exception as e:
        logger.error(f"Ошибка напоминания: {e}")

@client.on(events.NewMessage(pattern=r'!remind (\d+)\s+(.+)', func=owner_filter))
async def remind_cmd(e):
    if check_cover(e): return
    delay = int(e.pattern_match.group(1))
    text = e.pattern_match.group(2).strip()
    await respond(e, f"⏰ Напоминание через **{fmt_time(delay)}**\n📝 _{text}_")
    asyncio.create_task(send_reminder(e.chat_id, text, delay))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!search (.+)', func=owner_filter))
async def search_cmd(e):
    if check_cover(e): return
    q = e.pattern_match.group(1).strip()
    enc = q.replace(' ', '+')
    await respond(e, 
        f"🔍 **{q}**\n\n"
        f"• [Google](https://www.google.com/search?q={enc})\n"
        f"• [DuckDuckGo](https://duckduckgo.com/?q={enc})\n"
        f"• [YouTube](https://www.youtube.com/results?search_query={enc})\n"
        f"• [Wikipedia](https://ru.wikipedia.org/wiki/Special:Search?search={enc})"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!shorten (.+)', func=owner_filter))
async def shorten_cmd(e):
    if check_cover(e): return
    url = e.pattern_match.group(1).strip()
    await respond(e, "⏳ Сокращаю...")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://tinyurl.com/api-create.php?url={url}",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                short = await r.text()
        if short.startswith('http'):
            await respond(e, f"✂️ **Оригинал:** `{url[:55]}{'…' if len(url) > 55 else ''}`\n🔗 **Короткая:** {short.strip()}")
        else:
            raise Exception()
    except Exception:
        await respond(e, "❌ Ошибка. Проверь URL.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!weather (.+)', func=owner_filter))
async def weather_cmd(e):
    if check_cover(e): return
    city = e.pattern_match.group(1).strip()
    enc = city.replace(' ', '+')
    await respond(e, 
        f"🌤️ **Погода: {city}**\n\n"
        f"• [wttr.in](https://wttr.in/{enc})\n"
        f"• [OpenWeatherMap](https://openweathermap.org/find?q={enc})\n"
        f"• [Weather.com](https://weather.com/ru-RU/weather/today/l/{enc})"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!translate (.+)', func=owner_filter))
async def translate_cmd(e):
    if check_cover(e): return
    text = e.pattern_match.group(1).strip()
    enc = text.replace(' ', '%20')
    await respond(e, 
        f"🌐 **Перевод:** _{text}_\n\n"
        f"• [RU→EN](https://translate.google.com/?sl=ru&tl=en&text={enc})\n"
        f"• [EN→RU](https://translate.google.com/?sl=en&tl=ru&text={enc})\n"
        f"• [Auto→RU](https://translate.google.com/?sl=auto&tl=ru&text={enc})"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!base64 (encode|decode) (.+)', func=owner_filter))
async def base64_cmd(e):
    if check_cover(e): return
    mode, text = e.pattern_match.group(1), e.pattern_match.group(2).strip()
    try:
        if mode == 'encode':
            res = base64.b64encode(text.encode()).decode()
            await respond(e, f"🔐 **Base64 encode:**\n`{res}`")
        else:
            res = base64.b64decode(text.encode()).decode()
            await respond(e, f"🔓 **Base64 decode:**\n`{res}`")
    except Exception as ex:
        logger.error(f"base64 error: {ex}")
        await respond(e, "❌ Ошибка. Проверь данные.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!hash (.+)', func=owner_filter))
async def hash_cmd(e):
    if check_cover(e): return
    text = e.pattern_match.group(1).strip().encode()
    await respond(e, 
        f"#️⃣ **Хэши**\n\n"
        f"MD5:    `{hashlib.md5(text).hexdigest()}`\n"
        f"SHA1:   `{hashlib.sha1(text).hexdigest()}`\n"
        f"SHA256: `{hashlib.sha256(text).hexdigest()}`\n"
        f"SHA512: `{hashlib.sha512(text).hexdigest()[:64]}…`"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!morse (.+)', func=owner_filter))
async def morse_cmd(e):
    if check_cover(e): return
    text = e.pattern_match.group(1).strip()
    await respond(e, f"📡 **Морзе:**\n_{text}_\n\n`{morse_enc(text)}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!caesar (encode|decode) (\d+) (.+)', func=owner_filter))
async def caesar_cmd(e):
    if check_cover(e): return
    mode, shift, text = e.pattern_match.group(1), int(e.pattern_match.group(2)), e.pattern_match.group(3)
    res = caesar(text, shift, dec=(mode == 'decode'))
    await respond(e, f"{'🔒' if mode == 'encode' else '🔓'} **Цезарь (сдвиг {shift}):**\n_{text}_\n\n`{res}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!vigenere (encode|decode) (\S+) (.+)', func=owner_filter))
async def vigenere_cmd(e):
    if check_cover(e): return
    mode, key, text = e.pattern_match.group(1), e.pattern_match.group(2), e.pattern_match.group(3)
    res = vigenere(text, key, dec=(mode == 'decode'))
    await respond(e, f"{'🔒' if mode == 'encode' else '🔓'} **Виженер (ключ: {key}):**\n_{text}_\n\n`{res}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!password(?:\s+(\d+))?(?:\s+(simple))?$', func=owner_filter))
async def password_cmd(e):
    if check_cover(e): return
    length = max(4, min(int(e.pattern_match.group(1) or 16), 128))
    sym = not e.pattern_match.group(2)
    pwd = gen_pwd(length, sym)
    s = "🔴 Слабый" if length < 8 else "🟡 Средний" if length < 12 else "🟢 Сильный" if length < 20 else "💎 Очень сильный"
    await respond(e, f"🔑 **Пароль ({length} симв.)**\n\n`{pwd}`\n\nСила: {s}\nСимволы: {'✅' if sym else '❌'}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!qr (.+)', func=owner_filter))
async def qr_cmd(e):
    if check_cover(e): return
    text = e.pattern_match.group(1).strip().replace(' ', '+')
    await respond(e, 
        f"📱 **QR-код**\n\n"
        f"🔗 [Открыть изображение](https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={text})"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!uuid$', func=owner_filter))
async def uuid_cmd(e):
    if check_cover(e): return
    ids = [str(uuid.uuid4()) for _ in range(5)]
    out = "\n".join(f"`{u}`" for u in ids)
    await respond(e, f"🆔 **Случайные UUID v4:**\n\n{out}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!color (#[0-9a-fA-F]{6}|\d+,\d+,\d+)', func=owner_filter))
async def color_cmd(e):
    if check_cover(e): return
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
    l_val = (mx + mn) / 2
    if mx == mn:
        s_val = 0.0
        h_val = 0.0
    else:
        denom = 1 - abs(2 * l_val - 1)
        s_val = 0 if denom == 0 else (mx - mn) / denom
        if mx == rf:
            h_val = 60 * ((gf - bf) / (mx - mn) % 6)
        elif mx == gf:
            h_val = 60 * ((bf - rf) / (mx - mn) + 2)
        else:
            h_val = 60 * ((rf - gf) / (mx - mn) + 4)
    await respond(e, 
        f"🎨 **Цвет**\n\n"
        f"HEX: `{hex_val}`\n"
        f"RGB: `rgb({r}, {g}, {b})`\n"
        f"HSL: `hsl({h_val:.0f}°, {s_val * 100:.0f}%, {l_val * 100:.0f}%)`\n\n"
        f"🔗 [Превью](https://www.colorhexa.com/{hex_val.lstrip('#')})"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!ascii (.+)', func=owner_filter))
async def ascii_cmd(e):
    if check_cover(e): return
    text = e.pattern_match.group(1).strip()
    codes = ' '.join(str(ord(c)) for c in text)
    back = ''.join(chr(int(x)) for x in codes.split())
    await respond(e, f"🔢 **ASCII коды:**\n_{text}_\n\n`{codes}`\n\nОбратно: `{back}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!type(?:\s+(fast|slow|matrix|glitch))?\s+(.+)', func=owner_filter))
async def type_cmd(e):
    if check_cover(e): return
    mode = e.pattern_match.group(1) or 'normal'
    text = e.pattern_match.group(2).strip()
    if mode == 'fast':
        msg = await respond(e, "▌")
        for i in range(0, len(text), 2):
            chunk = text[:i + 2]
            await msg.edit(chunk + ("▌" if i + 2 < len(text) else ""))
            await asyncio.sleep(0.04)
        await msg.edit(text)
    elif mode == 'slow':
        msg = await respond(e, "▌")
        shown = ""
        for ch in text:
            shown += ch
            await msg.edit(shown + "▌")
            pause = 0.3 if ch in '.!?…' else 0.12 if ch in ',;:' else 0.07
            await asyncio.sleep(pause)
        await msg.edit(text)
    elif mode == 'matrix':
        CHARS = string.ascii_letters + string.digits + "@#%&"
        msg = await respond(e, "▓" * len(text))
        for step in range(len(text)):
            parts = list(text[:step])
            for _ in range(len(text) - step):
                parts.append(random.choice(CHARS))
            await msg.edit(''.join(parts))
            await asyncio.sleep(0.07)
        await msg.edit(text)
    elif mode == 'glitch':
        GLITCH = "░▒▓█▄▀■□▪▫"
        msg = await respond(e, "".join(random.choice(GLITCH) for _ in text))
        for _ in range(6):
            glitched = "".join(
                c if random.random() > 0.4 else random.choice(GLITCH)
                for c in text
            )
            await msg.edit(glitched)
            await asyncio.sleep(0.12)
        await msg.edit(text)
    else:
        msg = await respond(e, "▌")
        shown = ""
        for i, ch in enumerate(text):
            shown += ch
            if i % 2 == 0 or i == len(text) - 1:
                await msg.edit(shown + ("▌" if i < len(text) - 1 else ""))
                await asyncio.sleep(0.05)
        await msg.edit(text)
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!echo (.+)', func=owner_filter))
async def echo_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, e.pattern_match.group(1).strip())
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!say (.+)', func=owner_filter))
async def say_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, e.pattern_match.group(1).strip())
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!bold (.+)', func=owner_filter))
async def bold_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, f"**{e.pattern_match.group(1).strip()}**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!italic (.+)', func=owner_filter))
async def italic_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, f"__{e.pattern_match.group(1).strip()}__")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!mono (.+)', func=owner_filter))
async def mono_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, f"`{e.pattern_match.group(1).strip()}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!clean(?:\s+(\d+))?$', func=owner_filter))
async def clean_cmd(e):
    if check_cover(e): return
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
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!purge(?:\s+(\d+))?$', func=owner_filter))
async def purge_cmd(e):
    if check_cover(e): return
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
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!spam (\d+) (.+)', func=owner_filter))
async def spam_cmd(e):
    if check_cover(e): return
    count, text = int(e.pattern_match.group(1)), e.pattern_match.group(2).strip()
    MAX_SPAM = 50
    if count < 1 or count > MAX_SPAM:
        await respond(e, f"❌ Допустимо от 1 до {MAX_SPAM} сообщений.")
        return
    cooldown_key = f'spam_{e.chat_id}'
    remaining = command_cooldown.get(cooldown_key, 0) - time.time()
    if remaining > 0:
        await respond(e, f"⏳ Подождите {int(remaining)} сек перед повторным спамом.")
        return
    command_cooldown[cooldown_key] = time.time() + 30
    await e.delete()
    for _ in range(count):
        await client.send_message(e.chat_id, text)
        await asyncio.sleep(0.35)
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!forward (-?\d+)', func=owner_filter))
async def forward_cmd(e):
    if check_cover(e): return
    if not e.reply_to_msg_id:
        await respond(e, "ℹ️ Ответьте на сообщение: `!forward [chat_id]`")
        return
    try:
        msg = await e.get_reply_message()
        await client.forward_messages(int(e.pattern_match.group(1)), msg)
        await respond(e, f"✅ Переслано в `{e.pattern_match.group(1)}`")
    except Exception as ex:
        await respond(e, f"❌ {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!pin$', func=owner_filter))
async def pin_cmd(e):
    if check_cover(e): return
    if not e.reply_to_msg_id:
        await respond(e, "ℹ️ Ответьте на сообщение")
        return
    await (await e.get_reply_message()).pin(notify=False)
    await e.delete()
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!unpin$', func=owner_filter))
async def unpin_cmd(e):
    if check_cover(e): return
    if e.reply_to_msg_id:
        await (await e.get_reply_message()).unpin()
    else:
        await client.unpin_message(e.chat_id)
    await e.delete()
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!copyall (\d+) (-?\d+)', func=owner_filter))
async def copyall_cmd(e):
    if check_cover(e): return
    count, target = int(e.pattern_match.group(1)), int(e.pattern_match.group(2))
    await respond(e, f"⏳ Копирую {count} сообщений...")
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
    await respond(e, f"✅ Скопировано **{copied}/{count}** → `{target}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!react (.+)', func=owner_filter))
async def react_cmd(e):
    if check_cover(e): return
    if not e.reply_to_msg_id:
        await respond(e, "ℹ️ Ответьте на сообщение: `!react 👍`")
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
        await respond(e, f"❌ Не удалось поставить реакцию: {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'^!save$', func=owner_filter))
async def save_media_cmd(e):
    if check_cover(e): return
    if not e.reply_to_msg_id:
        await respond(e, "ℹ️ Ответьте на фото/видео или используйте `!save key value`")
        return
    replied = await e.get_reply_message()
    if not replied.media:
        await respond(e, "❌ В ответном сообщении нет медиа.")
        return
    os.makedirs(MEDIA_DIR, exist_ok=True)
    try:
        path = await replied.download_media(os.path.join(MEDIA_DIR, ''))
        name = os.path.basename(path) if path else 'unknown'
        db.set_saved(f'_media_{int(time.time())}', name)
        await respond(e, f"✅ **Сохранено:** `{name}`")
        logger.info(f"Media saved: {name}")
    except Exception as ex:
        await respond(e, f"❌ Ошибка сохранения: {ex}")
        logger.error(f"Save media error: {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!save (\S+) (.+)', func=owner_filter))
async def save_cmd(e):
    if check_cover(e): return
    k, v = e.pattern_match.group(1), e.pattern_match.group(2)
    db.set_saved(k, v)
    await respond(e, f"✅ `{k}` = _{v}_")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!get (\S+)', func=owner_filter))
async def get_cmd(e):
    if check_cover(e): return
    k = e.pattern_match.group(1)
    v = db.get_saved(k)
    await respond(e, f"📦 `{k}` = _{v}_" if v else f"❌ Ключ `{k}` не найден")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!del (\S+)', func=owner_filter))
async def del_cmd(e):
    if check_cover(e): return
    k = e.pattern_match.group(1)
    v = db.get_saved(k)
    if v is not None:
        db.del_saved(k)
        await respond(e, f"🗑 Удалено: `{k}`")
    else:
        await respond(e, f"❌ `{k}` не найден")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!list$', func=owner_filter))
async def list_cmd(e):
    if check_cover(e): return
    d = db.all_saved()
    if not d:
        await respond(e, "📭 Нет данных")
        db.bump_stat('cmds')
        return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v) > 40 else ''}_" for k, v in d.items())
    await respond(e, f"📦 **Сохранено ({len(d)}):**\n\n{items}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!find (.+)', func=owner_filter))
async def find_cmd(e):
    if check_cover(e): return
    query = e.pattern_match.group(1).strip().lower()
    saved_results = db.search_saved(query)
    notes_results = db.search_notes(query)
    lines = []
    if saved_results:
        lines.append(f"📦 **В сохранённом:**")
        for row in saved_results:
            lines.append(f"  • `{row['key']}` — _{row['value'][:40]}{'…' if len(row['value']) > 40 else ''}_")
    if notes_results:
        lines.append(f"📝 **В заметках:**")
        for row in notes_results:
            lines.append(f"  • `{row['key']}` — _{row['value'][:40]}{'…' if len(row['value']) > 40 else ''}_")
    if not lines:
        await respond(e, "🔍 **Ничего не найдено**")
    else:
        await respond(e, f"🔍 **Результаты поиска: {query}**\n\n" + "\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!note (\S+)(?: (.+))?', func=owner_filter))
async def note_cmd(e):
    if check_cover(e): return
    k = e.pattern_match.group(1)
    t = e.pattern_match.group(2) or ""
    if e.reply_to_msg_id:
        r = await e.get_reply_message()
        t = r.text or t
    if not t:
        await respond(e, "ℹ️ `!note <название> <текст>` или ответом")
        return
    db.set_note(k, t)
    await respond(e, f"📝 Заметка сохранена: `{k}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!getnote (\S+)', func=owner_filter))
async def getnote_cmd(e):
    if check_cover(e): return
    k = e.pattern_match.group(1)
    v = db.get_note(k)
    if v is not None:
        await respond(e, f"📝 **{k}:**\n\n{v}")
    else:
        await respond(e, f"❌ Заметка `{k}` не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!delnote (\S+)', func=owner_filter))
async def delnote_cmd(e):
    if check_cover(e): return
    k = e.pattern_match.group(1)
    v = db.get_note(k)
    if v is not None:
        db.del_note(k)
        await respond(e, f"🗑 Заметка удалена: `{k}`")
    else:
        await respond(e, f"❌ `{k}` не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!notes$', func=owner_filter))
async def notes_cmd(e):
    if check_cover(e): return
    d = db.all_notes()
    if not d:
        await respond(e, "📭 Нет заметок")
        db.bump_stat('cmds')
        return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v) > 40 else ''}_" for k, v in d.items())
    await respond(e, f"📝 **Заметки ({len(d)}):**\n\n{items}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!todo (.+)', func=owner_filter))
async def todo_add_cmd(e):
    if check_cover(e): return
    task = e.pattern_match.group(1).strip()
    db.add_todo(task)
    todos = db.get_todos()
    await respond(e, f"✅ Задача добавлена: _{task}_\n📋 Всего: {len(todos)}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!todos$', func=owner_filter))
async def todos_cmd(e):
    if check_cover(e): return
    todos = db.get_todos()
    if not todos:
        await respond(e, "📭 Список задач пуст")
        db.bump_stat('cmds')
        return
    lines = []
    for i, t in enumerate(todos, 1):
        mark = "✅" if t['done'] else "⬜"
        lines.append(f"{mark} {i}. _{t['text']}_")
    done = sum(1 for t in todos if t['done'])
    await respond(e, f"📋 **Список задач** ({done}/{len(todos)} выполнено):\n\n" + "\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!done (\d+)', func=owner_filter))
async def done_cmd(e):
    if check_cover(e): return
    idx = int(e.pattern_match.group(1)) - 1
    todos = db.get_todos()
    if 0 <= idx < len(todos):
        db.update_todo(todos[idx]['id'], done=True)
        await respond(e, f"✅ Выполнено: _{todos[idx]['text']}_")
    else:
        await respond(e, f"❌ Задача #{idx + 1} не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!undone (\d+)', func=owner_filter))
async def undone_cmd(e):
    if check_cover(e): return
    idx = int(e.pattern_match.group(1)) - 1
    todos = db.get_todos()
    if 0 <= idx < len(todos):
        db.update_todo(todos[idx]['id'], done=False)
        await respond(e, f"⬜ Снята отметка: _{todos[idx]['text']}_")
    else:
        await respond(e, f"❌ Задача #{idx + 1} не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!deltodo (\d+)', func=owner_filter))
async def deltodo_cmd(e):
    if check_cover(e): return
    idx = int(e.pattern_match.group(1)) - 1
    todos = db.get_todos()
    if 0 <= idx < len(todos):
        db.del_todo(todos[idx]['id'])
        await respond(e, f"🗑 Удалена задача: _{todos[idx]['text']}_")
    else:
        await respond(e, f"❌ Задача #{idx + 1} не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!afk(?:\s+(.+))?$', func=owner_filter))
async def afk_cmd(e):
    if check_cover(e): return
    reason = (e.pattern_match.group(1) or '').strip()
    state.set_afk(reason)
    r = f"\n📝 _{reason}_" if reason else ""
    await respond(e, f"😴 **AFK включён**{r}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!unafk$', func=owner_filter))
async def unafk_cmd(e):
    if check_cover(e): return
    dur = state.clear_afk()
    if dur is not None:
        await respond(e, f"☀️ **AFK выключен** | Отсутствовал: _{fmt_time(dur)}_")
    else:
        await respond(e, "ℹ️ AFK не был включён")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!chatinfo$', func=owner_filter))
async def chatinfo_cmd(e):
    if check_cover(e): return
    chat = await e.get_chat()
    name = getattr(chat, 'title', None) or f"{getattr(chat, 'first_name', '')} {getattr(chat, 'last_name', '')}".strip()
    uname = getattr(chat, 'username', None)
    members = getattr(chat, 'participants_count', None)
    lines = [
        f"📊 **Информация о чате**\n",
        f"📛 **{name}**",
        f"🆔 `{e.chat_id}`",
    ]
    if uname:
        lines.append(f"🔖 @{uname}")
    else:
        lines.append("🔖 Username: нет")
    lines.append(f"👥 Тип: `{type(chat).__name__}`")
    if members:
        lines.append(f"👤 Участников: `{members}`")
    await respond(e, "\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!members$', func=owner_filter))
async def members_cmd(e):
    if check_cover(e): return
    try:
        p = await client.get_participants(e.chat_id)
        bots = sum(1 for x in p if x.bot)
        await respond(e, f"👥 **Участники**\n\nВсего: `{len(p)}`\n👤 Людей: `{len(p) - bots}`\n🤖 Ботов: `{bots}`")
    except Exception as ex:
        await respond(e, f"❌ {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!admins$', func=owner_filter))
async def admins_cmd(e):
    if check_cover(e): return
    try:
        admins = await client.get_participants(e.chat_id, filter=ChannelParticipantsAdmins())
        lines = [f"👑 **Администраторы ({len(admins)}):**\n"]
        for a in admins[:25]:
            name = f"{a.first_name or ''} {a.last_name or ''}".strip()
            lines.append(f"• {name} — {'@' + a.username if a.username else '`' + str(a.id) + '`'}")
        await respond(e, "\n".join(lines))
    except Exception as ex:
        await respond(e, f"❌ {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!top(?:\s+(\d+))?$', func=owner_filter))
async def top_cmd(e):
    if check_cover(e): return
    limit = int(e.pattern_match.group(1) or 200)
    await respond(e, "⏳ Анализирую...")
    cnt, names = defaultdict(int), {}
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        if msg.sender_id:
            cnt[msg.sender_id] += 1
            if msg.sender_id not in names:
                s = await msg.get_sender()
                if s:
                    n = f"{getattr(s, 'first_name', '') or ''} {getattr(s, 'last_name', '') or ''}".strip()
                    names[msg.sender_id] = n or str(msg.sender_id)
    top = sorted(cnt.items(), key=lambda x: x[1], reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = [f"🏆 **Топ активных** (из {limit} сообщ.):\n"]
    for i, (uid, c) in enumerate(top):
        lines.append(f"{medals[i]} {names.get(uid, uid)} — `{c}` сообщ.")
    await respond(e, "\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!bots$', func=owner_filter))
async def bots_cmd(e):
    if check_cover(e): return
    try:
        bots = await client.get_participants(e.chat_id, filter=ChannelParticipantsBots())
        lines = [f"🤖 **Боты в чате ({len(bots)}):**\n"]
        for b in bots[:20]:
            lines.append(f"• @{b.username or b.id}")
        await respond(e, "\n".join(lines))
    except Exception as ex:
        await respond(e, f"❌ {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!resetdata$', func=owner_filter))
async def resetdata_cmd(e):
    if check_cover(e): return
    db.clear_all()
    state.auto_reply_enabled = False
    state.auto_reply_text = '💫 Я автоответчик, хозяин скоро ответит! Спасибо за терпение 😘'
    state.ghost_mode = False
    state.afk_start_time = None
    state.afk_reason = ''
    state.sudo_users.clear()
    state._save()
    await respond(e, "🧹 **Все данные сброшены.**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!ytshow\s+(.+)', func=owner_filter))
async def ytshow_cmd(e):
    if check_cover(e): return
    raw = e.pattern_match.group(1).strip()
    parts = raw.rsplit(None, 1)
    if len(parts) == 2 and parts[1].isdigit():
        url = parts[0]
        height = int(parts[1])
    else:
        url = raw
        height = None

    async def edit_fn(text):
        await respond(e, text)

    await edit_fn(f"⏳ Загружаю ({'авто' if not height else f'{height}p'})...")
    filename = await _run_download(edit_fn, url, mode='video', quality=height, timeout=600)
    if filename:
        await _send_and_clean(edit_fn, e.chat_id, filename, f"🎬 YouTube: {url}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!dl\s+(.+)', func=owner_filter))
async def dl_cmd(e):
    if check_cover(e): return
    url = e.pattern_match.group(1).strip()

    async def edit_fn(text):
        await respond(e, text)

    await edit_fn("⏳ Загрузка...")
    try:
        filename = await _run_download(edit_fn, url, mode='video', timeout=600)
        if filename:
            await _send_and_clean(edit_fn, e.chat_id, filename)
    except Exception as ex:
        await respond(e, f"❌ Ошибка: {ex}")
        logger.error(f"dl error: {ex}")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!playlist\s+(.+?)(?:\s+(\d+)(?:-(\d+))?)?$', func=owner_filter))
async def playlist_cmd(e):
    if check_cover(e): return
    g = e.pattern_match
    url = g.group(1).strip()
    start_num = None
    end_num = None
    if g.group(2):
        start_num = int(g.group(2))
        end_num = int(g.group(3)) if g.group(3) else start_num

    msg = await respond(e, "⏳ Получаю информацию о плейлисте...")
    try:
        def _get_playlist_info():
            opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'force_generic_extractor': False,
                'extractor_args': {'youtube': {'player_client': ['android', 'ios']}},
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title', '')
                entries = info.get('entries', [])
                video_urls = []
                for entry in entries:
                    if entry and entry.get('url'):
                        video_urls.append(entry['url'])
                    elif entry and entry.get('webpage_url'):
                        video_urls.append(entry['webpage_url'])
                    elif entry and entry.get('id'):
                        video_urls.append(f'https://youtube.com/watch?v={entry["id"]}')
                return title, video_urls

        loop = asyncio.get_event_loop()
        title, video_urls = await loop.run_in_executor(None, _get_playlist_info)

        if not video_urls:
            await msg.edit("❌ Плейлист пуст или недоступен.")
            return

        total = len(video_urls)
        if start_num:
            s = max(1, start_num)
            e_idx = min(total, end_num) if end_num else min(total, s)
            selected = video_urls[s - 1:e_idx]
        else:
            selected = video_urls[:50]

        await msg.edit(f"📋 Плейлист: **{title or '?'}** ({len(selected)}/{total} видео)\n⏳ Начинаю загрузку...")

        for i, video_url in enumerate(selected, 1):
            vid_msg = await respond(e, f"⏳ [{i}/{len(selected)}] Загружаю видео {i}...")

            async def edit_vid(text, vid_msg=vid_msg):
                try:
                    await vid_msg.edit(text)
                except Exception:
                    pass

            filename = await _run_download(edit_vid, video_url, mode='video', timeout=600)
            if filename:
                await _send_and_clean(edit_vid, e.chat_id, filename, f"🎬 [{i}/{len(selected)}]")
                await asyncio.sleep(2)

        await respond(e, f"✅ **Плейлист загружен!** ({len(selected)}/{total} видео)")
    except Exception as ex:
        await respond(e, f"❌ Ошибка плейлиста: {ex}")
        logger.error(f"playlist error: {ex}")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!audio\s+(.+)', func=owner_filter))
async def audio_cmd(e):
    if check_cover(e): return
    url = e.pattern_match.group(1).strip()

    async def edit_fn(text):
        await respond(e, text)

    try:
        filename = await _run_download(edit_fn, url, mode='audio', timeout=600)
        if filename:
            await _send_and_clean(edit_fn, e.chat_id, filename, f"🎵 Аудио")
    except Exception as ex:
        await respond(e, f"❌ Ошибка: {ex}")
        logger.error(f"audio error: {ex}")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!sub\s+(.+?)(?:\s+(\w{2}))?$', func=owner_filter))
async def sub_cmd(e):
    if check_cover(e): return
    g = e.pattern_match
    url = g.group(1).strip()
    lang = (g.group(2) or 'ru').lower()

    msg = await respond(e, f"⏳ Ищу субтитры ({lang})...")
    try:
        def _get_captions():
            out_dir = os.path.join(MEDIA_DIR, 'subtmp')
            os.makedirs(out_dir, exist_ok=True)
            opts = {
                'quiet': True,
                'no_warnings': True,
                'extractor_args': {'youtube': {'player_client': ['android', 'ios']}},
                'writesubtitles': True,
                'subtitleslangs': [lang],
                'subtitlesformat': 'srt',
                'skip_download': True,
                'outtmpl': os.path.join(out_dir, '%(id)s'),
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                subs = info.get('subtitles') or info.get('requested_subtitles') or {}
                if lang not in subs:
                    for code in subs:
                        if code.startswith(lang):
                            lang_found = code
                            break
                    else:
                        return None
                else:
                    lang_found = lang
                sub_data = subs[lang_found]
                sub_url = None
                if isinstance(sub_data, list):
                    for entry in sub_data:
                        if entry.get('ext') == 'srt' and entry.get('url'):
                            sub_url = entry['url']
                            break
                    if not sub_url and sub_data and sub_data[0].get('url'):
                        sub_url = sub_data[0]['url']
                elif isinstance(sub_data, dict):
                    sub_url = sub_data.get('url')
                if sub_url:
                    r = requests.get(sub_url, timeout=30)
                    r.raise_for_status()
                    return r.text
                return None

        loop = asyncio.get_event_loop()
        srt_content = await loop.run_in_executor(None, _get_captions)

        if srt_content:
            sub_path = os.path.join(MEDIA_DIR, f'sub_{lang}.srt')
            with open(sub_path, 'w', encoding='utf-8') as f:
                f.write(srt_content)
            await msg.edit(f"📤 Отправляю субтитры ({lang})...")
            await client.send_file(e.chat_id, sub_path, caption=f"📝 Субтитры ({lang})")
            await asyncio.sleep(3)
            try:
                os.remove(sub_path)
            except OSError:
                pass
            await msg.edit(f"✅ Субтитры ({lang}) отправлены.")
        else:
            await msg.edit(f"❌ Субтитры ({lang}) не найдены для этого видео.")
    except Exception as ex:
        await msg.edit(f"❌ Ошибка: {ex}")
        logger.error(f"sub error: {ex}")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!watch\s+(on|off)$', func=owner_filter))
async def watch_cmd(e):
    global _watch_task
    arg = e.pattern_match.group(1)
    if arg == 'on':
        if _watch_task and not _watch_task.done():
            await respond(e, "⚠️ Мониторинг уже запущен.")
            return
        db.clear_sessions()
        try:
            result = await client(GetAuthorizationsRequest())
            for auth in result.authorizations:
                h = hashlib.md5(f"{auth.hash}{auth.device_model}{auth.platform}".encode()).hexdigest()
                db.save_session(h, json.dumps({"device": auth.device_model, "platform": auth.platform, "ip": auth.ip, "date": str(auth.date_created)}))
        except Exception as ex:
            logger.warning(f"Init sessions: {ex}")

        async def monitor():
            while True:
                try:
                    result = await client(GetAuthorizationsRequest())
                    known = db.all_sessions()
                    for auth in result.authorizations:
                        h = hashlib.md5(f"{auth.hash}{auth.device_model}{auth.platform}".encode()).hexdigest()
                        if h not in known:
                            db.save_session(h, json.dumps({"device": auth.device_model, "platform": auth.platform, "ip": auth.ip, "date": str(auth.date_created)}))
                            me = await client.get_me()
                            await client.send_message(me.id, f"⚠️ **Новый вход**\nУстройство: {auth.device_model}\nПлатформа: {auth.platform}\nIP: {auth.ip}\nДата: {auth.date_created}")
                            logger.warning(f"New session: {auth.device_model} {auth.ip}")
                except Exception as ex:
                    logger.error(f"Watch error: {ex}")
                await asyncio.sleep(300)

        _watch_task = asyncio.create_task(monitor())
        await respond(e, "👁️ **Мониторинг сессий ВКЛЮЧЁН.** Проверка каждые 5 мин.")
    else:
        if _watch_task and not _watch_task.done():
            _watch_task.cancel()
            _watch_task = None
        await respond(e, "👁️ **Мониторинг сессий ВЫКЛЮЧЕН.**")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!check_email\s+(\S+)', func=owner_filter))
async def check_email_cmd(e):
    if check_cover(e): return
    email = e.pattern_match.group(1).strip().lower()
    msg = await respond(e, f"🔍 Проверяю {email}...")
    try:
        headers = {'hibp-api-key': HIBP_API_KEY, 'User-Agent': 'TelegramUserBot/1.0'}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                f'https://haveibeenpwned.com/api/v3/breachedaccount/{email}',
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 404:
                    await msg.edit(f"✅ **{email}** не найден в известных утечках.")
                elif resp.status == 200:
                    data = await resp.json()
                    lines = [f"⚠️ **{email}** найден в {len(data)} утечках:\n"]
                    for breach in data[:15]:
                        lines.append(f"• {breach.get('Name', '?')} ({breach.get('BreachDate', '?')})")
                    if len(data) > 15:
                        lines.append(f"… и ещё {len(data) - 15}")
                    await msg.edit("\n".join(lines))
                elif resp.status == 429:
                    await msg.edit("❌ Слишком много запросов к API. Попробуйте позже.")
                else:
                    await msg.edit(f"❌ Ошибка API: HTTP {resp.status}")
    except asyncio.TimeoutError:
        await msg.edit("❌ Таймаут запроса к HIBP.")
    except Exception as ex:
        await msg.edit(f"❌ Ошибка: {ex}")
        logger.error(f"check_email error: {ex}")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!protect\s+(on|off)$', func=owner_filter))
async def protect_cmd(e):
    global _protect_task
    arg = e.pattern_match.group(1)
    if arg == 'on':
        if _protect_task and not _protect_task.done():
            await respond(e, "⚠️ Защита уже включена.")
            return
        dialogs = await client.get_dialogs(limit=1000)
        db.clear_protected_chats()
        for d in dialogs:
            db.add_protected_chat(d.id)
        await respond(e, f"🔒 **Защита ВКЛЮЧЕНА.** Отслеживается {len(dialogs)} чатов.")

        async def monitor():
            while True:
                try:
                    current = {d.id for d in await client.get_dialogs(limit=1000)}
                    protected = set(db.get_protected_chat_ids())
                    missing = protected - current
                    for cid in missing:
                        me = await client.get_me()
                        await client.send_message(me.id, f"⚠️ **Удалён чат**\nID: `{cid}`\nЧат был удалён или вы из него вышли.")
                        db.del_protected_chat(cid)
                except Exception as ex:
                    logger.error(f"Protect monitor error: {ex}")
                await asyncio.sleep(120)

        _protect_task = asyncio.create_task(monitor())
    else:
        if _protect_task and not _protect_task.done():
            _protect_task.cancel()
            _protect_task = None
        db.clear_protected_chats()
        await respond(e, "🔓 **Защита ВЫКЛЮЧЕНА.**")
    db.bump_stat('cmds')


@client.on(events.NewMessage(func=lambda e: e.is_private))
async def private_handler(event):
    sender = await event.get_sender()
    if not sender:
        return

    logger.info(f"📩 [ЛС] От {sender.first_name} (id:{sender.id}), текст: {event.raw_text[:50]}")
    if event.reply_to_msg_id:
        logger.info(f"↩️ Ответ на сообщение ID:{event.reply_to_msg_id}")

    uid = sender.id
    now = time.time()

    if len(reply_cooldown) > MAX_COOLDOWN_ENTRIES:
        cutoff = now - 3600
        reply_cooldown = {k: v for k, v in reply_cooldown.items() if v > cutoff}

    if state.mute_enabled:
        logger.info(f"🔇 Mute — игнорирую {uid}")
        return

    if state.lock_enabled:
        try:
            me = await client.get_me()
            if uid == me.id:
                pass
            else:
                is_contact = db.get_saved(f'_lock_cache_{uid}')
                if is_contact is None:
                    is_contact = '0'
                    try:
                        contact = await client.get_entity(uid)
                        if getattr(contact, 'contact', False):
                            is_contact = '1'
                    except Exception:
                        pass
                    if is_contact == '0':
                        try:
                            common = await client.get_common_chats(uid)
                            if common:
                                is_contact = '1'
                        except Exception:
                            pass
                    db.set_saved(f'_lock_cache_{uid}', is_contact)
                if is_contact == '0':
                    logger.info(f"🔒 Lock: {uid} не контакт и нет общих чатов — игнорирую")
                    return
        except Exception as ex:
            logger.warning(f"Lock check error for {uid}: {ex}")

    silent_mode = state.silent_enabled

    if state.readreceipt_enabled:
        try:
            await client.send_read_acknowledge(event.chat_id, event.message)
        except Exception:
            pass

    if event.reply_to_msg_id and not event.out:
        try:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.sender_id:
                raw_text = event.raw_text.strip()
                lines = raw_text.split('\n', 1)
                cmd_part = lines[0].strip().lower()
                custom_reply = lines[1].strip() if len(lines) > 1 else None
                if cmd_part in RP_COMMANDS:
                    target_entity = await client.get_entity(reply_msg.sender_id)
                    target_name = target_entity.first_name or "пользователь"
                    user_name = sender.first_name or "Кто-то"
                    action_text = format_rp_action(cmd_part, user_name, target_name)
                    reply_text = custom_reply or get_rp_reply(cmd_part)
                    if state.typing_enabled:
                        async with client.action(event.chat_id, 'typing'):
                            await asyncio.sleep(0.8)
                    sent = await safe_flood(lambda: event.reply(f"{action_text}\n{reply_text}"))
                    logger.info(f"✅ RP (in): {user_name} -> {target_name} ({cmd_part})")
                    if state.ghost_mode or state.shadow_enabled:
                        asyncio.create_task(shadow_delete_msg(sent))
                    if state.autodel_enabled:
                        asyncio.create_task(shadow_delete_msg(sent, state.autodel_delay))
                    db.bump_stat('cmds')
                    return
        except Exception as e:
            logger.error(f"RP error (in): {e}")

    if state.afk_start_time and now - reply_cooldown.get(f'afk_{uid}', 0) > 60:
        if silent_mode:
            logger.info(f"🔇 Silent — AFK ответ скрыт для {uid}")
        else:
            dur = fmt_time(now - state.afk_start_time)
            reason_part = f"\n📝 _{state.afk_reason}_" if state.afk_reason else ""
            reply_cooldown[f'afk_{uid}'] = now
            if state.typing_enabled:
                async with client.action(event.chat_id, 'typing'):
                    await asyncio.sleep(0.8)
            sent = await safe_flood(lambda: event.reply(f"😴 Хозяин AFK уже **{dur}**{reason_part}"))
            if state.ghost_mode or state.shadow_enabled:
                asyncio.create_task(shadow_delete_msg(sent))
            if state.autodel_enabled:
                asyncio.create_task(shadow_delete_msg(sent, state.autodel_delay))

    if state.auto_reply_enabled and now - reply_cooldown.get(uid, 0) > 10:
        reply_text = db.get_reply_text(uid)
        if reply_text is None:
            reply_text = db.get_default_reply()
        if reply_text is None:
            reply_text = state.auto_reply_text if state.auto_reply_text else None
        if reply_text and not silent_mode:
            reply_cooldown[uid] = now
            if state.reply_delay:
                await asyncio.sleep(state.reply_delay)
            if state.typing_enabled:
                async with client.action(event.chat_id, 'typing'):
                    await asyncio.sleep(0.8)
            sent = await safe_flood(lambda: event.reply(reply_text))
            if state.ghost_mode or state.shadow_enabled:
                asyncio.create_task(shadow_delete_msg(sent))
            if state.autodel_enabled:
                asyncio.create_task(shadow_delete_msg(sent, state.autodel_delay))
        elif silent_mode:
            logger.info(f"🔇 Silent — автоответ скрыт для {uid}")

async def safe_flood(coro_factory, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except FloodWaitError as e:
            logger.warning(f"FloodWait: {e.seconds}s, попытка {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                await asyncio.sleep(e.seconds + 1)
            else:
                raise


async def shadow_delete_msg(msg, delay=None):
    d = delay or state.shadow_delay or 5
    await asyncio.sleep(d)
    try:
        await msg.delete()
    except Exception:
        pass

@client.on(events.NewMessage(outgoing=True, func=lambda e: e.is_private and e.reply_to_msg_id))
async def rp_outgoing_handler(event):
    if event.sender_id != OWNER_ID and event.sender_id not in state.sudo_users:
        return
    try:
        reply_msg = await event.get_reply_message()
        if not reply_msg or not reply_msg.sender_id:
            return
        raw_text = event.raw_text.strip()
        lines = raw_text.split('\n', 1)
        cmd_part = lines[0].strip().lower()
        if cmd_part not in RP_COMMANDS:
            return
        custom_reply = lines[1].strip() if len(lines) > 1 else None
        target_entity = await client.get_entity(reply_msg.sender_id)
        target_name = target_entity.first_name or "пользователь"
        me = await client.get_me()
        user_name = me.first_name or "Кто-то"
        action_text = format_rp_action(cmd_part, user_name, target_name)
        reply_text = custom_reply or get_rp_reply(cmd_part)
        if state.typing_enabled:
            async with client.action(event.chat_id, 'typing'):
                await asyncio.sleep(0.8)
        sent = await safe_flood(lambda: event.reply(f"{action_text}\n{reply_text}"))
        logger.info(f"✅ RP: {user_name} -> {target_name} ({cmd_part}) | реплика: {'кастом' if custom_reply else 'рандом'}")
        if state.ghost_mode or state.shadow_enabled:
            asyncio.create_task(shadow_delete_msg(sent))
        if state.autodel_enabled:
            asyncio.create_task(shadow_delete_msg(sent, state.autodel_delay))
        db.bump_stat('cmds')
    except Exception as e:
        logger.error(f"RP error: {e}")

@client.on(events.NewMessage(pattern=r'^!rphelp$', func=lambda e: e.is_private))
async def rphelp_cmd(event):
    sender = await event.get_sender()
    if not sender:
        return
    lines = ["📚 **Доступные RP-команды**\n"]
    for category in get_all_categories():
        cmds = get_category_commands(category)
        if cmds:
            lines.append(f"\n**{category.upper()}**:")
            for i in range(0, len(cmds), 4):
                chunk = cmds[i:i + 4]
                lines.append("  " + "  ".join(f"`{c}`" for c in chunk))
    lines.append("\n💡 Используй: `команда` в ответ на сообщение")
    lines.append("💬 Можно добавить реплику через Enter:\n   `обнять` + Enter + `Текст реплики`")
    await event.reply("\n".join(lines))
    db.bump_stat('cmds')

CMD_DESCS = {
    # основные
    'sleep':       {'desc': 'Включить автоответчик', 'syntax': '!sleep', 'example': '!sleep'},
    'wake':        {'desc': 'Выключить автоответчик', 'syntax': '!wake', 'example': '!wake'},
    'setreply':    {'desc': 'Установить текст ответа', 'syntax': '!setreply [@user|default] [текст]', 'example': '!setreply @username Привет'},
    'status':      {'desc': 'Полный статус бота', 'syntax': '!status', 'example': '!status'},
    'time':        {'desc': 'Текущее время и дата', 'syntax': '!time', 'example': '!time'},
    'ping':        {'desc': 'Проверить задержку', 'syntax': '!ping', 'example': '!ping'},
    'id':          {'desc': 'ID чата / пользователя', 'syntax': '!id', 'example': '!id'},
    'info':        {'desc': 'Информация о боте', 'syntax': '!info', 'example': '!info'},
    'restart':     {'desc': 'Перезапустить бота', 'syntax': '!restart', 'example': '!restart'},
    'ghost':       {'desc': 'Ghost-режим (автоудаление команд)', 'syntax': '!ghost', 'example': '!ghost'},
    'resetdata':   {'desc': 'Сбросить все данные ⚠️', 'syntax': '!resetdata', 'example': '!resetdata'},
    # стелс
    'cover':       {'desc': 'Игнор всех команд кроме !cover off', 'syntax': '!cover [on|off]', 'example': '!cover on'},
    'silent':      {'desc': 'Бот молчит в ЛС', 'syntax': '!silent [on|off]', 'example': '!silent on'},
    'shadow':      {'desc': 'Автоудаление ответов через N сек', 'syntax': '!shadow [сек]', 'example': '!shadow 10'},
    'lock':        {'desc': 'Отвечать только контактам', 'syntax': '!lock [on|off]', 'example': '!lock on'},
    'mute':        {'desc': 'Игнорировать все ЛС', 'syntax': '!mute [on|off]', 'example': '!mute on'},
    'typing':      {'desc': 'Показывать «печатает…» перед ответом', 'syntax': '!typing [on|off]', 'example': '!typing on'},
    'autodel':     {'desc': 'Автоудаление всех исходящих сообщений', 'syntax': '!autodel [on|off] [сек]', 'example': '!autodel on 10'},
    'delay':       {'desc': 'Задержка перед автоответом (симуляция человека)', 'syntax': '!delay [сек]', 'example': '!delay 3'},
    'readreceipt': {'desc': 'Отмечать ЛС прочитанными', 'syntax': '!readreceipt [on|off]', 'example': '!readreceipt off'},
    'online':      {'desc': 'Установить статус «онлайн»', 'syntax': '!online', 'example': '!online'},
    'offline':     {'desc': 'Установить статус «недавно»', 'syntax': '!offline', 'example': '!offline'},
    'status_reset':{'desc': 'Сбросить все стелс-режимы', 'syntax': '!status_reset', 'example': '!status_reset'},
    # профиль
    'me':           {'desc': 'Мой профиль', 'syntax': '!me', 'example': '!me'},
    'avatar':       {'desc': 'Получить аватарку', 'syntax': '!avatar', 'example': '!avatar'},
    'name':         {'desc': 'Сменить имя', 'syntax': '!name [имя]', 'example': '!name НовоеИмя'},
    'lastname':     {'desc': 'Сменить фамилию', 'syntax': '!lastname [фамилия]', 'example': '!lastname Иванов'},
    'bio':          {'desc': 'Обновить «о себе»', 'syntax': '!bio [текст]', 'example': '!bio Люблю котиков'},
    'whois':        {'desc': 'Информация о пользователе', 'syntax': '!whois @ник', 'example': '!whois @durov'},
    'username_check': {'desc': 'Проверить занятость username', 'syntax': '!username_check @ник', 'example': '!username_check @test'},
    # игры
    'dice':         {'desc': 'Кинуть кубик', 'syntax': '!dice', 'example': '!dice'},
    'dart':         {'desc': 'Кинуть дротик', 'syntax': '!dart', 'example': '!dart'},
    'basket':       {'desc': 'Бросить мяч', 'syntax': '!basket', 'example': '!basket'},
    'football':     {'desc': 'Удар по мячу', 'syntax': '!football', 'example': '!football'},
    'bowling':      {'desc': 'Боулинг', 'syntax': '!bowling', 'example': '!bowling'},
    'casino':       {'desc': 'Игровой автомат', 'syntax': '!casino', 'example': '!casino'},
    'coin':         {'desc': 'Подбросить монетку', 'syntax': '!coin', 'example': '!coin'},
    'rand':         {'desc': 'Случайное число', 'syntax': '!rand [a] [b]', 'example': '!rand 1 100'},
    '8ball':        {'desc': 'Магический шар', 'syntax': '!8ball [вопрос]', 'example': '!8ball Сегодня будет удача?'},
    'rps':          {'desc': 'Камень-ножницы-бумага', 'syntax': '!rps [к/н/б]', 'example': '!rps камень'},
    'slot':         {'desc': 'Слот-машина', 'syntax': '!slot', 'example': '!slot'},
    'lucky':        {'desc': 'Индекс удачи', 'syntax': '!lucky', 'example': '!lucky'},
    'choose':       {'desc': 'Случайный выбор', 'syntax': '!choose [вар1, вар2]', 'example': '!choose пицца, суши'},
    'quiz':         {'desc': 'Викторина', 'syntax': '!quiz', 'example': '!quiz'},
    # youtube
    'ytshow':       {'desc': 'Скачать видео с YouTube', 'syntax': '!ytshow <URL> [качество]', 'example': '!ytshow https://youtu.be/... 720'},
    'dl':           {'desc': 'Скачать видео (YouTube / Instagram / TikTok)', 'syntax': '!dl <URL>', 'example': '!dl https://www.tiktok.com/@user/video/...'},
    'playlist':     {'desc': 'Скачать плейлист YouTube', 'syntax': '!playlist <URL> [кол-во] | [start-end]', 'example': '!playlist https://youtube.com/playlist?list=... 5'},
    'audio':        {'desc': 'Скачать аудио с YouTube', 'syntax': '!audio <URL>', 'example': '!audio https://youtu.be/...'},
    'sub':          {'desc': 'Скачать субтитры с YouTube', 'syntax': '!sub <URL> [язык]', 'example': '!sub https://youtu.be/... ru'},
    # утилиты
    'calc':         {'desc': 'Калькулятор', 'syntax': '!calc [выражение]', 'example': '!calc 2+2*2'},
    'remind':       {'desc': 'Напоминание', 'syntax': '!remind [сек] [текст]', 'example': '!remind 60 Поставить чайник'},
    'search':       {'desc': 'Поисковики', 'syntax': '!search [запрос]', 'example': '!search Python'},
    'shorten':      {'desc': 'Сократить ссылку', 'syntax': '!shorten [url]', 'example': '!shorten https://example.com'},
    'weather':      {'desc': 'Ссылки на погоду', 'syntax': '!weather [город]', 'example': '!weather Москва'},
    'translate':    {'desc': 'Ссылки на перевод', 'syntax': '!translate [текст]', 'example': '!translate Hello'},
    'base64':       {'desc': 'Base64 кодирование/декодирование', 'syntax': '!base64 encode|decode [текст]', 'example': '!base64 encode Привет'},
    'hash':         {'desc': 'Хэши (MD5/SHA)', 'syntax': '!hash [текст]', 'example': '!hash password'},
    'morse':        {'desc': 'Азбука Морзе', 'syntax': '!morse [текст]', 'example': '!morse SOS'},
    'caesar':       {'desc': 'Шифр Цезаря', 'syntax': '!caesar encode|decode [сдвиг] [текст]', 'example': '!caesar encode 3 Привет'},
    'vigenere':     {'desc': 'Шифр Виженера', 'syntax': '!vigenere encode|decode [ключ] [текст]', 'example': '!vigenere encode key Привет'},
    'password':     {'desc': 'Генератор паролей', 'syntax': '!password [длина] [simple]', 'example': '!password 20'},
    'qr':           {'desc': 'QR-код', 'syntax': '!qr [текст]', 'example': '!qr https://example.com'},
    'uuid':         {'desc': 'Генератор UUID', 'syntax': '!uuid', 'example': '!uuid'},
    'color':        {'desc': 'Информация о цвете', 'syntax': '!color [#HEX или R,G,B]', 'example': '!color #FF0000'},
    'ascii':        {'desc': 'ASCII коды символов', 'syntax': '!ascii [текст]', 'example': '!ascii Hello'},
    # сообщения
    'type':         {'desc': 'Печать текста с эффектом', 'syntax': '!type [fast|slow|matrix|glitch] [текст]', 'example': '!type slow Привет'},
    'echo':         {'desc': 'Отправить сообщение', 'syntax': '!echo [текст]', 'example': '!echo Тест'},
    'say':          {'desc': 'Отправить сообщение (алиас)', 'syntax': '!say [текст]', 'example': '!say Привет'},
    'bold':         {'desc': 'Жирный текст', 'syntax': '!bold [текст]', 'example': '!bold Важно'},
    'italic':       {'desc': 'Курсивный текст', 'syntax': '!italic [текст]', 'example': '!italic Цитата'},
    'mono':         {'desc': 'Моноширинный текст', 'syntax': '!mono [текст]', 'example': '!mono code'},
    'clean':        {'desc': 'Удалить свои N сообщений', 'syntax': '!clean [n]', 'example': '!clean 5'},
    'purge':        {'desc': 'Удалить любые N сообщений', 'syntax': '!purge [n]', 'example': '!purge 10'},
    'spam':         {'desc': 'Спам N сообщений', 'syntax': '!spam [n] [текст]', 'example': '!spam 5 Привет'},
    'forward':      {'desc': 'Переслать сообщение в чат', 'syntax': '!forward [chat_id]', 'example': '!forward -100123456789'},
    'pin':          {'desc': 'Закрепить сообщение', 'syntax': '!pin', 'example': '!pin'},
    'unpin':        {'desc': 'Открепить сообщение', 'syntax': '!unpin', 'example': '!unpin'},
    'copyall':      {'desc': 'Копировать N сообщений в чат', 'syntax': '!copyall [n] [chat_id]', 'example': '!copyall 50 -100123456789'},
    'react':        {'desc': 'Поставить реакцию', 'syntax': '!react [эмодзи]', 'example': '!react 👍'},
    # заметки
    'save':         {'desc': 'Сохранить значение по ключу / сохранить медиа', 'syntax': '!save <ключ> <значение> | !save (в ответ на медиа)', 'example': '!save пароль 12345'},
    'get':          {'desc': 'Получить значение по ключу', 'syntax': '!get <ключ>', 'example': '!get пароль'},
    'del':          {'desc': 'Удалить значение по ключу', 'syntax': '!del <ключ>', 'example': '!del пароль'},
    'list':         {'desc': 'Список всех сохранённых данных', 'syntax': '!list', 'example': '!list'},
    'find':         {'desc': 'Поиск по сохранённым данным и заметкам', 'syntax': '!find <слово>', 'example': '!find пароль'},
    'note':         {'desc': 'Сохранить заметку', 'syntax': '!note <название> <текст>', 'example': '!note Идея Купить молоко'},
    'getnote':      {'desc': 'Получить заметку', 'syntax': '!getnote <название>', 'example': '!getnote Идея'},
    'delnote':      {'desc': 'Удалить заметку', 'syntax': '!delnote <название>', 'example': '!delnote Идея'},
    'notes':        {'desc': 'Список всех заметок', 'syntax': '!notes', 'example': '!notes'},
    'todo':         {'desc': 'Добавить задачу', 'syntax': '!todo <текст>', 'example': '!todo Купить молоко'},
    'todos':        {'desc': 'Список всех задач', 'syntax': '!todos', 'example': '!todos'},
    'done':         {'desc': 'Отметить задачу выполненной', 'syntax': '!done <номер>', 'example': '!done 1'},
    'undone':       {'desc': 'Снять отметку выполнения', 'syntax': '!undone <номер>', 'example': '!undone 1'},
    'deltodo':      {'desc': 'Удалить задачу', 'syntax': '!deltodo <номер>', 'example': '!deltodo 1'},
    # afk
    'afk':          {'desc': 'Включить AFK-режим', 'syntax': '!afk [причина]', 'example': '!afk Сплю'},
    'unafk':        {'desc': 'Выключить AFK-режим', 'syntax': '!unafk', 'example': '!unafk'},
    # инфо
    'chatinfo':     {'desc': 'Информация о чате', 'syntax': '!chatinfo', 'example': '!chatinfo'},
    'members':      {'desc': 'Количество участников', 'syntax': '!members', 'example': '!members'},
    'admins':       {'desc': 'Список администраторов', 'syntax': '!admins', 'example': '!admins'},
    'top':          {'desc': 'Топ активных пользователей', 'syntax': '!top [n]', 'example': '!top 100'},
    'bots':         {'desc': 'Список ботов в чате', 'syntax': '!bots', 'example': '!bots'},
    # безопасность
    'sudo':         {'desc': 'Управление sudo-пользователями', 'syntax': '!sudo [on|off] @user', 'example': '!sudo on @durov'},
    'watch':        {'desc': 'Мониторинг новых сессий', 'syntax': '!watch [on|off]', 'example': '!watch on'},
    'check_email':  {'desc': 'Проверить email на утечки (HIBP)', 'syntax': '!check_email <email>', 'example': '!check_email test@example.com'},
    'protect':      {'desc': 'Защита от удаления чатов', 'syntax': '!protect [on|off]', 'example': '!protect on'},
    # rp
    'rphelp':       {'desc': 'Список RP-команд', 'syntax': '!rphelp', 'example': '!rphelp'},
}

COMMANDS_LIST = {
    'основные': [
        '!sleep', '!wake', '!setreply', '!status', '!time', '!ping',
        '!id', '!info', '!restart', '!ghost', '!resetdata'
    ],
    'стелс': [
        '!cover', '!silent', '!shadow', '!lock', '!mute',
        '!typing', '!autodel', '!delay', '!readreceipt',
        '!online', '!offline', '!status_reset'
    ],
    'профиль': [
        '!me', '!avatar', '!name', '!lastname', '!bio', '!whois', '!username_check'
    ],
    'игры': [
        '!dice', '!dart', '!basket', '!football', '!bowling', '!casino',
        '!coin', '!rand', '!8ball', '!rps', '!slot', '!lucky', '!choose', '!quiz'
    ],
    'youtube': [
        '!ytshow', '!dl', '!playlist', '!audio', '!sub'
    ],
    'утилиты': [
        '!calc', '!remind', '!search', '!shorten', '!weather', '!translate',
        '!base64', '!hash', '!morse', '!caesar', '!vigenere', '!password',
        '!qr', '!uuid', '!color', '!ascii'
    ],
    'сообщения': [
        '!type', '!echo', '!say', '!bold', '!italic', '!mono',
        '!clean', '!purge', '!spam', '!forward', '!pin', '!unpin',
        '!copyall', '!react'
    ],
    'заметки': [
        '!save', '!get', '!del', '!list', '!find',
        '!note', '!getnote', '!delnote', '!notes',
        '!todo', '!todos', '!done', '!undone', '!deltodo'
    ],
    'безопасность': [
        '!sudo', '!watch', '!check_email', '!protect'
    ],
    'afk': ['!afk', '!unafk'],
    'инфо': ['!chatinfo', '!members', '!admins', '!top', '!bots'],
    'rp': ['!rphelp'],
}

EMOJI_MAP = {
    'основные': '⚙️', 'стелс': '🛡️', 'профиль': '👤', 'игры': '🎮',
    'youtube': '🎬', 'утилиты': '🛠', 'сообщения': '✉️', 'заметки': '📦',
    'безопасность': '🔐', 'afk': '😴', 'инфо': '📊', 'rp': '🎭',
}

HELP_CATS = {
    'основные': (
        "⚙️ **ОСНОВНЫЕ КОМАНДЫ**\n\n"
        "`!sleep` — включить автоответчик\n"
        "`!wake` — выключить автоответчик\n"
        "`!setreply [@user | default] [текст]` — текст ответа\n"
        "`!status` — полный статус бота\n"
        "`!time` — время и дата\n"
        "`!ping` — задержка соединения\n"
        "`!id` — ID чата / пользователя\n"
        "`!info` — информация о боте\n"
        "`!restart` — перезапуск\n"
        "`!ghost` — ghost-режим\n"
        "`!resetdata` — сброс всех данных ⚠️"
    ),
    'стелс': (
        "🛡️ **СТЕЛС-РЕЖИМЫ**\n\n"
        "`!cover [on|off]` — игнор всех команд\n"
        "`!silent [on|off]` — бот молчит в ЛС\n"
        "`!shadow [сек]` — автоудаление ответов\n"
        "`!lock [on|off]` — только контактам\n"
        "`!mute [on|off]` — игнор всех ЛС\n"
        "`!typing [on|off]` — показывать «печатает…»\n"
        "`!autodel [on|off] [сек]` — автовудаление исходящих\n"
        "`!delay [сек]` — задержка перед автоответом\n"
        "`!readreceipt [on|off]` — отмечать прочитанным\n"
        "`!online` — статус «онлайн»\n"
        "`!offline` — статус «недавно»\n"
        "`!status_reset` — сброс всех стелс-режимов"
    ),
    'профиль': (
        "👤 **ПРОФИЛЬ**\n\n"
        "`!me` — свой профиль\n"
        "`!avatar` — своя/чужая аватарка\n"
        "`!name [имя]` — сменить имя\n"
        "`!lastname [фамилия]` — сменить фамилию\n"
        "`!bio [текст]` — обновить «о себе»\n"
        "`!whois @ник` — инфо о пользователе\n"
        "`!username_check @ник` — проверить username"
    ),
    'игры': (
        "🎮 **ИГРЫ И РАЗВЛЕЧЕНИЯ**\n\n"
        "`!dice` `!dart` `!basket` `!football` `!bowling` `!casino` — анимации TG\n"
        "`!coin` — монетка\n"
        "`!rand` — случайное число\n"
        "`!8ball [вопрос]` — магический шар\n"
        "`!rps [к/н/б]` — камень-ножницы-бумага\n"
        "`!slot` — слот-машина\n"
        "`!lucky` — индекс удачи\n"
        "`!choose [вар1 | вар2]` — случайный выбор\n"
        "`!quiz` — викторина"
    ),
    'youtube': (
        "🎬 **ЗАГРУЗКА МЕДИА**\n\n"
        "`!ytshow <URL> [качество]` — скачать видео с YouTube\n"
        "`!dl <URL>` — скачать видео (YouTube / Instagram / TikTok)\n"
        "`!playlist <URL> [кол-во | start-end]` — загрузка плейлиста YouTube\n"
        "`!audio <URL>` — скачать аудио с YouTube\n"
        "`!sub <URL> [ru|en]` — скачать субтитры с YouTube\n\n"
        "💡 Таймаут — 10 мин."
    ),
    'утилиты': (
        "🛠 **УТИЛИТЫ**\n\n"
        "`!calc [выражение]` — калькулятор\n"
        "`!remind [сек] [текст]` — напоминание\n"
        "`!search [запрос]` — поисковики\n"
        "`!shorten [url]` — сократить ссылку\n"
        "`!weather [город]` — погода\n"
        "`!translate [текст]` — перевод\n"
        "`!base64 encode/decode [текст]`\n"
        "`!hash [текст]` — MD5/SHA хэши\n"
        "`!morse [текст]` — азбука Морзе\n"
        "`!caesar encode/decode [сдвиг] [текст]`\n"
        "`!vigenere encode/decode [ключ] [текст]`\n"
        "`!password [длина] [simple]`\n"
        "`!qr [текст]` — QR-код\n"
        "`!uuid` — UUID v4\n"
        "`!color [#HEX или R,G,B]`\n"
        "`!ascii [текст]`"
    ),
    'сообщения': (
        "✉️ **СООБЩЕНИЯ**\n\n"
        "`!type [fast/slow/matrix/glitch] [текст]`\n"
        "`!echo [текст]` / `!say [текст]`\n"
        "`!bold` `!italic` `!mono` [текст]\n"
        "`!clean [n]` — удалить свои N сообщений\n"
        "`!purge [n]` — удалить любые N сообщений\n"
        "`!spam [n] [текст]`\n"
        "`!forward [chat_id]`\n"
        "`!pin` / `!unpin`\n"
        "`!copyall [n] [chat_id]`\n"
        "`!react [эмодзи]`\n\n"
        "📎 **Сохранение медиа:** ответьте на фото/видео → `!save`"
    ),
    'заметки': (
        "📦 **ЗАМЕТКИ И TODO**\n\n"
        "**Хранилище:** `!save key val` `!get` `!del` `!list` `!find`\n"
        "**Медиа:** ответ на фото/видео → `!save`\n"
        "**Заметки:** `!note` `!getnote` `!delnote` `!notes`\n"
        "**TODO:** `!todo` `!todos` `!done` `!undone` `!deltodo`"
    ),
    'безопасность': (
        "🔐 **БЕЗОПАСНОСТЬ**\n\n"
        "`!sudo [on|off] @user` — управление sudo-доступом\n"
        "`!watch [on|off]` — мониторинг новых сессий\n"
        "`!check_email <email>` — проверка утечек через HIBP\n"
        "`!protect [on|off]` — защита от удаления чатов"
    ),
    'afk': (
        "😴 **AFK**\n\n"
        "`!afk [причина]` — включить AFK-режим\n"
        "`!unafk` — выключить с отчётом времени"
    ),
    'инфо': (
        "📊 **ИНФОРМАЦИЯ О ЧАТЕ**\n\n"
        "`!chatinfo` — информация о чате\n"
        "`!members` — количество участников\n"
        "`!admins` — список администраторов\n"
        "`!top [n]` — топ активных\n"
        "`!bots` — список ботов"
    ),
    'rp': (
        "🎭 **RP-КОМАНДЫ (ролевые)**\n\n"
        "Напишите в ответ (reply) одно слово — бот отправит действие.\n"
        "Через Enter можно добавить свою реплику.\n\n"
        "**Список команд:** `!rphelp`\n\n"
        "**Пример:** ответьте `обнять` + Enter + `Ты моя сладкая`\n"
        "→ отправит `🤗 ...обнял... Ты моя сладкая`\n\n"
        "**Категории:**\n" + '\n'.join(
            f'• {cat.capitalize()}: {", ".join(get_category_commands(cat))}'
            for cat in get_all_categories()
        )
    ),
}

@client.on(events.NewMessage(pattern=r'!help(?:\s+(.+))?$', func=owner_filter))
async def help_cmd(e):
    if check_cover(e): return
    arg = (e.pattern_match.group(1) or '').strip().lower()

    if arg.startswith('cmd '):
        cmd_name = arg[4:].strip().lstrip('!')
        if cmd_name in CMD_DESCS:
            d = CMD_DESCS[cmd_name]
            lines = [
                f"📖 **Команда:** `!{cmd_name}`",
                f"**Описание:** {d['desc']}",
                f"**Синтаксис:** `{d['syntax']}`",
                f"**Пример:** `{d['example']}`",
            ]
            lines.append("\n💡 Для справки по команде: `!help cmd <команда>`")
            lines.append("💡 Для всех команд: `!help all`")
            await respond(e, "\n".join(lines))
            db.bump_stat('cmds')
            return
        else:
            await respond(e, f"❌ Команда `{cmd_name}` не найдена.\n💡 `!help cmd <команда>`")
            db.bump_stat('cmds')
            return

    if arg == 'all':
        msg = "📋 **Все команды:**\n"
        for cat, cmds in COMMANDS_LIST.items():
            emoji = EMOJI_MAP.get(cat, '•')
            cmds_str = ", ".join(f"`{cmd}`" for cmd in cmds)
            msg += f"\n{emoji} **{cat.capitalize()}:** {cmds_str}\n"
        msg += "\n💡 Для справки по команде: `!help cmd <команда>`\n💡 Для всех категорий: `!help <категория>`"
        if len(msg) > 4096:
            msg = msg[:4080] + "\n\n⚠️ Сообщение обрезано (лимит 4096)"
        await respond(e, msg)
        db.bump_stat('cmds')
        return

    if arg:
        cat = arg
        if cat not in HELP_CATS:
            cats = ', '.join(f"`!help {c}`" for c in HELP_CATS)
            await respond(e, f"❌ Категория `{cat}` не найдена.\n\nДоступные категории:\n{cats}")
            db.bump_stat('cmds')
            return
        text = HELP_CATS[cat]
        cmds = COMMANDS_LIST.get(cat, [])
        if cmds:
            text += "\n\n📋 **Команды для копирования:**\n" + ", ".join(f"`{cmd}`" for cmd in cmds)
        text += "\n\n💡 Для справки по команде: `!help cmd <команда>`\n💡 Для всех команд: `!help all`"
        await respond(e, text)
        db.bump_stat('cmds')
        return

    lines = ["📚 **UserBot Help**\n\nВыбери категорию — скопируй команду и отправь:\n"]
    for cat_name in HELP_CATS:
        emoji = EMOJI_MAP.get(cat_name, '•')
        lines.append(f"{emoji} `{cat_name.capitalize()}` → `!help {cat_name}`")
    lines.append("\n💡 Для справки по команде: `!help cmd <команда>`")
    lines.append("💡 Для всех команд: `!help all`")
    await respond(e, "\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!commands$', func=owner_filter))
async def commands_cmd(e):
    if check_cover(e): return
    await respond(e, "ℹ️ Используйте `!help all` для списка всех команд с описанием.")
    db.bump_stat('cmds')

if __name__ == "__main__":
    print("🚀 Запуск UserBot (с RP-командами)...")
    os.makedirs(MEDIA_DIR, exist_ok=True)
    Thread(target=run_web, daemon=True).start()
    client.start()
    print("✅ Бот запущен! Логи в консоли.")
    client.run_until_disconnected()
