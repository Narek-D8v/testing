import os
import logging
import datetime
import asyncio
import random
import re
import math
import time
import hashlib
import base64
import string
import uuid

import subprocess
import shutil

import aiohttp
import yt_dlp
from collections import defaultdict

from telethon import TelegramClient, events
from telethon.tl.types import (
    InputMediaDice, ChannelParticipantsAdmins, ChannelParticipantsBots,
    ReactionEmoji
)
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.functions.account import UpdateProfileRequest, UpdateStatusRequest, GetAuthorizationsRequest
from flask import Flask
from threading import Thread

from storage import Storage
from rp_commands import RP_COMMANDS, get_rp_reply, format_rp_action, get_all_categories, get_category_commands


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_ID = os.environ.get('API_ID')
API_HASH = os.environ.get('API_HASH')
PORT = int(os.environ.get('PORT', 8080))
STRING_SESSION = os.environ.get('STRING_SESSION')

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

    def reset_stealth(self):
        self.cover_enabled = False
        self.silent_enabled = False
        self.shadow_enabled = False
        self.shadow_delay = 5
        self.lock_enabled = False
        self.mute_enabled = False
        self._save()

state = BotState()
command_cooldown = defaultdict(float)
reply_cooldown = defaultdict(float)
is_downloading = False
_watch_task = None
_protect_task = None


def format_bytes(n):
    for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


def _detect_ffmpeg():
    path = shutil.which('ffmpeg')
    if path:
        return os.path.dirname(path)
    if os.path.exists('/usr/bin/ffmpeg'):
        return '/usr/bin'
    return None


def _find_cookies():
    paths = [
        os.path.join(os.path.dirname(__file__), 'cookies.txt'),
        os.path.join(os.getcwd(), 'cookies.txt'),
        'cookies.txt',
    ]
    for p in paths:
        if os.path.exists(p):
            size = os.path.getsize(p)
            print(f"[cookies] found: {p} ({size} bytes)")
            try:
                with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                    head = f.read(200)
                if 'youtube.com' not in head and 'youtu.be' not in head:
                    print(f"[cookies] WARNING: no youtube.com in cookies file")
                if not head.startswith('#') and '{' in head[:50]:
                    print(f"[cookies] WARNING: looks like JSON, need Netscape format!")
            except Exception as e:
                print(f"[cookies] read error: {e}")
            return p
    print(f"[cookies] NOT FOUND in any path: {paths}")
    return None


async def _run_download(event_edit_func, url, ydl_opts, timeout=600):
    global is_downloading
    if is_downloading:
        await event_edit_func("⏳ Уже идёт другая загрузка. Подождите.")
        return None
    is_downloading = True
    print(f"[_run_download] Starting: {url}")
    try:
        last_progress_update = 0

        def hook(d):
            nonlocal last_progress_update
            status = d['status']
            print(f"[hook] status={status}")
            if status == 'downloading':
                now = time.time()
                if now - last_progress_update < 3:
                    return
                last_progress_update = now
                total = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 0)
                down = d.get('downloaded_bytes', 0)
                speed = d.get('speed', 0)
                pct = (down / total * 100) if total > 0 else 0
                eta = d.get('eta', 0)
                pct_str = f"{pct:.0f}%"
                speed_str = f"{speed / 1024 / 1024:.1f} MB/s" if speed else "N/A"
                eta_str = fmt_time(eta) if eta else "N/A"
                text = f"📥 **Скачивание...** {pct_str}\n⬇ {speed_str} | ⏱ ~{eta_str}"
                print(f"[progress] {pct_str} {speed_str} ETA {eta_str}")
                logger.info(f"Download progress: {pct_str} {speed_str} ETA {eta_str}")
                try:
                    client.loop.create_task(event_edit_func(text))
                except Exception as e:
                    print(f"[hook] create_task error: {e}")
                    pass
            elif status == 'finished':
                fn = d.get('filename', '')
                print(f"[hook] finished: {fn}")
                logger.info(f"Download finished: {fn}")

        ydl_opts['progress_hooks'] = [hook]
        ydl_opts['quiet'] = True
        ydl_opts['no_warnings'] = True
        ydl_opts['noplaylist'] = True
        ydl_opts['nocheckcertificate'] = True
        ydl_opts['cachedir'] = False
        ydl_opts['extractor_args'] = {
            'youtube': {
                'player_client': ['android', 'web', 'ios', 'tv', 'android_creator'],
                'skip': ['hls', 'dash', 'js', 'webpage'],
                'player_skip': ['configs'],
            }
        }
        ydl_opts['socket_timeout'] = 30
        ydl_opts['retries'] = 10
        ydl_opts['fragment_retries'] = 10
        ydl_opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        ffmpeg_dir = _detect_ffmpeg()
        if ffmpeg_dir:
            ydl_opts['ffmpeg_location'] = ffmpeg_dir
            print(f"[ffmpeg] found at {ffmpeg_dir}")
        else:
            print(f"[ffmpeg] not found, DASH may fall back to best")
        if 'bestvideo+' in str(ydl_opts.get('format', '')):
            ydl_opts['merge_output_format'] = 'mp4'
            ydl_opts['postprocessor_args'] = ['-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k']
        cookies_path = _find_cookies()
        if cookies_path:
            ydl_opts['cookiefile'] = cookies_path

        def _dl():
            print("[_dl] creating YoutubeDL...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                print("[_dl] extract_info...")
                info = ydl.extract_info(url, download=True)
                fn = ydl.prepare_filename(info)
                print(f"[_dl] done, filename={fn}")
                return fn

        loop = asyncio.get_event_loop()
        task = loop.run_in_executor(None, _dl)
        print("[_run_download] waiting for task...")
        filename = await asyncio.wait_for(task, timeout=timeout)
        if filename and os.path.exists(filename):
            print(f"[_run_download] file saved: {filename} ({os.path.getsize(filename)} bytes)")
        return filename
    except asyncio.TimeoutError:
        print(f"[_run_download] TIMEOUT after {timeout}s: {url}")
        await event_edit_func("❌ Превышено время ожидания (10 мин).")
        logger.warning(f"Download timeout for {url}")
        return None
    except Exception as ex:
        err_str = str(ex)
        print(f"[_run_download] ERROR: {err_str}")
        import traceback
        traceback.print_exc()
        if "format not available" in err_str.lower() and 'bestvideo+' in str(ydl_opts.get('format', '')):
            print("[_dl] retrying with format='best'...")
            await event_edit_func("⚠️ DASH-формат недоступен, пробую best...")
            ydl_opts['format'] = 'best'
            ydl_opts.pop('merge_output_format', None)
            ydl_opts.pop('postprocessor_args', None)
            try:
                def _dl_retry():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        return ydl.prepare_filename(info)
                loop = asyncio.get_event_loop()
                task = loop.run_in_executor(None, _dl_retry)
                filename = await asyncio.wait_for(task, timeout=timeout)
                if filename and os.path.exists(filename):
                    print(f"[_run_download] retry OK: {filename}")
                return filename
            except Exception as ex2:
                print(f"[_dl] retry also failed: {ex2}")
                await event_edit_func(f"❌ **Ошибка:** {ex2}")
                logger.error(f"Download retry error: {ex2}")
                return None
        if "Sign in" in err_str or "confirm" in err_str.lower():
            cp = _find_cookies()
            if cp:
                await event_edit_func(f"⚠ YouTube блокирует IP (даже с cookies). Добавлен player_client=android для обхода.\nЕсли ошибка повторится — попробуйте обновить cookies.txt (экспорт в Netscape-формате) или сменить хостинг.\nФайл: {cp}")
            else:
                await event_edit_func(f"⚠ YouTube требует авторизации. Файл cookies.txt не найден.\nПроверенные пути:\n• {os.path.join(os.path.dirname(__file__), 'cookies.txt')}\n• {os.path.join(os.getcwd(), 'cookies.txt')}\n• cookies.txt\nИнструкция: https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp")
        elif "HTTP Error 429" in err_str:
            await event_edit_func("⚠ Слишком много запросов к YouTube. Попробуйте позже.")
        else:
            await event_edit_func(f"❌ **Ошибка:** {ex}")
        logger.error(f"Download error: {ex}")
        return None
    finally:
        is_downloading = False


async def _send_and_clean(event_edit_func, chat_id, filepath, caption=''):
    if not filepath or not os.path.exists(filepath):
        return
    size_mb = os.path.getsize(filepath) / 1024 / 1024
    if size_mb > 1500:
        await event_edit_func("❌ Слишком большой файл (>1.5 ГБ).")
        try:
            os.remove(filepath)
        except Exception:
            pass
        return
    if size_mb > 50:
        ffmpeg_dir = _detect_ffmpeg()
        if not ffmpeg_dir:
            await event_edit_func(f"⚠️ Файл {format_bytes(os.path.getsize(filepath))} (ffmpeg не найден, сжатие недоступно).")
        else:
            await event_edit_func(f"⚠️ Файл {format_bytes(os.path.getsize(filepath))}, сжимаю...")
            compressed = filepath.rsplit('.', 1)[0] + '_compressed.' + filepath.rsplit('.', 1)[-1]
            try:
                subprocess.run([
                    os.path.join(ffmpeg_dir, 'ffmpeg'), '-i', filepath, '-vf', 'scale=min(854,iw):min(480,ih)',
                    '-c:v', 'libx264', '-crf', '28', '-c:a', 'aac', '-y', compressed
                ], capture_output=True, timeout=120)
                os.remove(filepath)
                filepath = compressed
            except Exception:
                await event_edit_func("⚠️ Не удалось сжать, отправляю как есть.")
    await event_edit_func("📤 **Отправляю файл...**")
    try:
        await client.send_file(chat_id, filepath, caption=caption)
    except Exception as ex:
        await event_edit_func(f"❌ Ошибка отправки: {ex}")
    await asyncio.sleep(5)
    try:
        os.remove(filepath)
    except Exception:
        pass

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

@client.on(events.NewMessage(pattern=r'!sleep$', func=lambda e: e.sender_id == 5457847440))
async def sleep_cmd(e):
    if check_cover(e): return
    state.toggle_auto_reply(True)
    await e.edit('💤 Автоответчик **ВКЛЮЧЕН**.')
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!wake$', func=lambda e: e.sender_id == 5457847440))
async def wake_cmd(e):
    if check_cover(e): return
    state.toggle_auto_reply(False)
    await e.edit('☀️ Автоответчик **ВЫКЛЮЧЕН**.')
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!setreply(?:\s+(@\w+))?(?:\s+(.+))?', func=lambda e: e.sender_id == 5457847440))
async def setreply_cmd(e):
    if check_cover(e): return
    g = e.pattern_match
    target, text = g.group(1), g.group(2)
    if target and target.lower() == '@default':
        db.set_default_reply(text or '')
        await e.edit(f"✅ Дефолтный ответ установлен:\n_{text or 'пусто'}_")
    elif target:
        db.set_reply_text(target.lstrip('@'), text or '')
        await e.edit(f"✅ Ответ для {target} установлен:\n_{text or 'пусто'}_")
    elif text:
        state.set_auto_reply_text(text)
        await e.edit(f"✅ Текст автоответчика:\n_{text}_")
    else:
        await e.edit("ℹ️ `!setreply @username текст` или `!setreply default текст`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!status$', func=lambda e: e.sender_id == 5457847440))
async def status_cmd(e):
    if check_cover(e): return
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    s = db.all_stats()
    afk_status = f"✅ {state.afk_reason or 'без причины'}" if state.afk_start_time else "❌"
    await e.edit(
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
        f"😴 AFK: {afk_status}\n"
        f"⏱ Аптайм: `{state.uptime}`\n"
        f"📨 Команд выполнено: `{s.get('cmds', 0)}`"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!time$', func=lambda e: e.sender_id == 5457847440))
async def time_cmd(e):
    if check_cover(e): return
    now = datetime.datetime.now()
    utc = datetime.datetime.utcnow()
    week_days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
    await e.edit(
        f"🕐 **Время и дата**\n\n"
        f"🏠 Локальное: `{now.strftime('%H:%M:%S')}`\n"
        f"🌍 UTC: `{utc.strftime('%H:%M:%S')}`\n"
        f"📅 Дата: `{now.strftime('%d.%m.%Y')}`\n"
        f"📆 День: **{week_days[now.weekday()]}**"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!ping$', func=lambda e: e.sender_id == 5457847440))
async def ping_cmd(e):
    if check_cover(e): return
    t0 = time.monotonic()
    await e.edit("🏓 ...")
    ms = (time.monotonic() - t0) * 1000
    q = "🟢 Отлично" if ms < 150 else "🟡 Нормально" if ms < 400 else "🔴 Высокая"
    await e.edit(f"🏓 **Понг!**\n⚡ Задержка: `{ms:.1f} мс`\n📶 Качество: {q}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!id$', func=lambda e: e.sender_id == 5457847440))
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
    await e.edit("\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!info$', func=lambda e: e.sender_id == 5457847440))
async def info_cmd(e):
    if check_cover(e): return
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
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!restart$', func=lambda e: e.sender_id == 5457847440))
async def restart_cmd(e):
    if check_cover(e): return
    await e.edit('🔄 Перезагрузка...')
    await asyncio.sleep(2)
    await client.disconnect()
    os._exit(0)

@client.on(events.NewMessage(pattern=r'!ghost$', func=lambda e: e.sender_id == 5457847440))
async def ghost_cmd(e):
    if check_cover(e): return
    state.toggle_ghost()
    if state.ghost_mode:
        await e.edit("👻 **Ghost-режим ВКЛЮЧЁН** — команды удаляются мгновенно")
        await asyncio.sleep(2)
        await e.delete()
    else:
        await e.edit("👁 **Ghost-режим ВЫКЛЮЧЕН**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!cover(?:\s+(off|on))?$', func=lambda e: e.sender_id == 5457847440))
async def cover_cmd(e):
    arg = e.pattern_match.group(1)
    if arg == 'off':
        state.set_cover(False)
        await e.edit("🛡️ **Cover-режим ВЫКЛЮЧЕН** — команды снова работают.")
    else:
        state.set_cover(True)
        await e.edit("🛡️ **Cover-режим ВКЛЮЧЁН** — все команды, кроме `!cover off`, игнорируются.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!silent\s*(on|off)?$', func=lambda e: e.sender_id == 5457847440))
async def silent_cmd(e):
    arg = e.pattern_match.group(1)
    if arg == 'off':
        state.set_silent(False)
        await e.edit("🔇 **Silent-режим ВЫКЛЮЧЕН** — ответы снова отправляются.")
    else:
        state.set_silent(True)
        await e.edit("🔇 **Silent-режим ВКЛЮЧЁН** — бот молчит в ЛС.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!shadow(?:\s+(\d+))?$', func=lambda e: e.sender_id == 5457847440))
async def shadow_cmd(e):
    delay = e.pattern_match.group(1)
    if delay:
        d = int(delay)
        state.set_shadow(True, max(1, d))
        await e.edit(f"👤 **Shadow-режим ВКЛЮЧЁН** — удаление через {max(1, d)} сек.")
    elif state.shadow_enabled:
        state.set_shadow(False)
        await e.edit("👤 **Shadow-режим ВЫКЛЮЧЕН** — автодудаление отключено.")
    else:
        state.set_shadow(True)
        await e.edit("👤 **Shadow-режим ВКЛЮЧЁН** — удаление через 5 сек.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!lock(?:\s+(on|off))?$', func=lambda e: e.sender_id == 5457847440))
async def lock_cmd(e):
    arg = e.pattern_match.group(1)
    if arg == 'off':
        state.set_lock(False)
        await e.edit("🔒 **Lock-режим ВЫКЛЮЧЕН** — ЛС от всех открыты.")
    else:
        state.set_lock(True)
        await e.edit("🔒 **Lock-режим ВКЛЮЧЁН** — бот отвечает только контактам.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!mute(?:\s+(on|off))?$', func=lambda e: e.sender_id == 5457847440))
async def mute_cmd(e):
    arg = e.pattern_match.group(1)
    if arg == 'off':
        state.set_mute(False)
        await e.edit("🔇 **Mute-режим ВЫКЛЮЧЕН** — ЛС принимаются.")
    else:
        state.set_mute(True)
        await e.edit("🔇 **Mute-режим ВКЛЮЧЁН** — все ЛС игнорируются.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!online$', func=lambda e: e.sender_id == 5457847440))
async def online_cmd(e):
    if check_cover(e): return
    try:
        await client(UpdateStatusRequest(offline=False))
        await e.edit("🟢 Статус: **Онлайн**")
    except Exception as ex:
        await e.edit(f"❌ Ошибка: {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!offline$', func=lambda e: e.sender_id == 5457847440))
async def offline_cmd(e):
    if check_cover(e): return
    try:
        await client(UpdateStatusRequest(offline=True))
        await e.edit("🔴 Статус: **Недавно был(а)**")
    except Exception as ex:
        await e.edit(f"❌ Ошибка: {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!status_reset$', func=lambda e: e.sender_id == 5457847440))
async def status_reset_cmd(e):
    state.reset_stealth()
    await e.edit("🔄 **Все стелс-режимы сброшены**: cover, silent, shadow, lock, mute — выключены.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!me$', func=lambda e: e.sender_id == 5457847440))
async def me_cmd(e):
    if check_cover(e): return
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
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!avatar$', func=lambda e: e.sender_id == 5457847440))
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
        await e.edit("❌ Аватарка не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!name (.+)', func=lambda e: e.sender_id == 5457847440))
async def name_cmd(e):
    if check_cover(e): return
    n = e.pattern_match.group(1).strip()
    await client.edit_profile(first_name=n)
    await e.edit(f"✅ Имя → **{n}**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!lastname(?:\s+(.+))?$', func=lambda e: e.sender_id == 5457847440))
async def lastname_cmd(e):
    if check_cover(e): return
    n = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(last_name=n)
    await e.edit(f"✅ Фамилия → **{n}**" if n else "✅ Фамилия удалена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!bio(?:\s+(.+))?$', func=lambda e: e.sender_id == 5457847440))
async def bio_cmd(e):
    if check_cover(e): return
    t = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(about=t)
    await e.edit(f"✅ Био → _{t}_" if t else "✅ Био очищено")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!whois (.+)', func=lambda e: e.sender_id == 5457847440))
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
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!username_check (.+)', func=lambda e: e.sender_id == 5457847440))
async def username_check_cmd(e):
    if check_cover(e): return
    uname = e.pattern_match.group(1).strip().lstrip('@')
    try:
        ent = await client.get_entity(uname)
        name = getattr(ent, 'first_name', None) or getattr(ent, 'title', '?')
        await e.edit(f"🔍 @{uname}\n✅ **Занят**\n👤 {name}\n🆔 `{ent.id}`")
    except Exception:
        await e.edit(f"🔍 @{uname}\n✅ **Свободен**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!dice$', func=lambda e: e.sender_id == 5457847440))
async def dice_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎲'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!dart$', func=lambda e: e.sender_id == 5457847440))
async def dart_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎯'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!basket$', func=lambda e: e.sender_id == 5457847440))
async def basket_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🏀'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!football$', func=lambda e: e.sender_id == 5457847440))
async def football_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('⚽'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!bowling$', func=lambda e: e.sender_id == 5457847440))
async def bowling_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎳'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!casino$', func=lambda e: e.sender_id == 5457847440))
async def casino_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, file=InputMediaDice('🎰'))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!coin$', func=lambda e: e.sender_id == 5457847440))
async def coin_cmd(e):
    if check_cover(e): return
    sides = ["Орёл 🦅", "Решка 💰"]
    r = random.choice(sides)
    flips = random.randint(3, 9)
    await e.edit(f"🪙 Монета вращается {flips} раз...\n\nРезультат: **{r}**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!rand(?:\s+(-?\d+)(?:\s+(-?\d+))?)?$', func=lambda e: e.sender_id == 5457847440))
async def rand_cmd(e):
    if check_cover(e): return
    g = e.pattern_match
    a, b = g.group(1), g.group(2)
    if a and b:
        lo, hi = sorted([int(a), int(b)])
        await e.edit(f"🎲 `{lo}` … `{hi}` → **{random.randint(lo, hi)}**")
    elif a:
        await e.edit(f"🎲 `1` … `{a}` → **{random.randint(1, int(a))}**")
    else:
        await e.edit(f"🎲 **{random.randint(1, 100)}**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!8ball(?:\s+(.+))?$', func=lambda e: e.sender_id == 5457847440))
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
    msg = await e.edit("🎱 Шар вращается...")
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

@client.on(events.NewMessage(pattern=r'!rps(?:\s+(.+))?$', func=lambda e: e.sender_id == 5457847440))
async def rps_cmd(e):
    if check_cover(e): return
    MAP = {'к': '🪨 Камень', 'камень': '🪨 Камень', 'н': '✂️ Ножницы', 'ножницы': '✂️ Ножницы', 'б': '📄 Бумага', 'бумага': '📄 Бумага'}
    BOT = ['🪨 Камень', '✂️ Ножницы', '📄 Бумага']
    WIN = {'🪨 Камень': '✂️ Ножницы', '✂️ Ножницы': '📄 Бумага', '📄 Бумага': '🪨 Камень'}
    arg = (e.pattern_match.group(1) or '').lower().strip()
    if not arg or arg not in MAP:
        await e.edit("✊✌️🖐 `!rps камень` / `ножницы` / `бумага` (или `к`!`н`!`б`)")
        return
    uc, bc = MAP[arg], random.choice(BOT)
    if uc == bc:
        res = "🤝 **Ничья!**"
    elif WIN[uc] == bc:
        res = "🏆 **Ты победил!**"
    else:
        res = "💀 **Бот победил!**"
    await e.edit(f"✊✌️🖐 **КНБ**\n\n👤 Ты: {uc}\n🤖 Бот: {bc}\n\n{res}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!slot$', func=lambda e: e.sender_id == 5457847440))
async def slot_cmd(e):
    if check_cover(e): return
    SYM = ['🍒', '🍋', '🍊', '🍇', '🍉', '⭐', '💎', '7️⃣', '🔔', '🍀']
    msg = await e.edit("🎰 [ ▓ | ▓ | ▓ ]")
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

@client.on(events.NewMessage(pattern=r'!lucky$', func=lambda e: e.sender_id == 5457847440))
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
    await e.edit(f"🔮 **Индекс удачи**\n\n[{bar}] **{pct}%**\n\n{msg}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!choose (.+)', func=lambda e: e.sender_id == 5457847440))
async def choose_cmd(e):
    if check_cover(e): return
    raw = e.pattern_match.group(1)
    opts = [o.strip() for o in re.split(r'[,|/]', raw) if o.strip()]
    if len(opts) < 2:
        await e.edit("ℹ️ Перечисли варианты через запятую: `!choose пицца, суши, бургер`")
        return
    winner = random.choice(opts)
    listed = "\n".join(f"{'➡️' if o == winner else '  •'} {o}" for o in opts)
    await e.edit(f"🤔 **Выбираю из {len(opts)} вариантов...**\n\n{listed}\n\n✅ **Выбор: {winner}**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!quiz$', func=lambda e: e.sender_id == 5457847440))
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
    await e.edit(
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

@client.on(events.NewMessage(pattern=r'!calc (.+)', func=lambda e: e.sender_id == 5457847440))
async def calc_cmd(e):
    if check_cover(e): return
    expr = e.pattern_match.group(1).strip()
    r = await safe_eval(expr)
    if r is not None:
        await e.edit(f"🧮 `{expr}` = **{r}**")
    else:
        await e.edit("❌ Ошибка выражения. Разрешены: `+ - * / % sqrt sin cos tan log abs pow pi e factorial ceil floor round`")
    db.bump_stat('cmds')

async def send_reminder(chat_id, msg_text, delay):
    await asyncio.sleep(delay)
    try:
        await client.send_message(chat_id, f"⏰ **НАПОМИНАНИЕ:**\n{msg_text}")
    except Exception as e:
        logger.error(f"Ошибка напоминания: {e}")

@client.on(events.NewMessage(pattern=r'!remind (\d+)\s+(.+)', func=lambda e: e.sender_id == 5457847440))
async def remind_cmd(e):
    if check_cover(e): return
    delay = int(e.pattern_match.group(1))
    text = e.pattern_match.group(2).strip()
    await e.edit(f"⏰ Напоминание через **{fmt_time(delay)}**\n📝 _{text}_")
    asyncio.create_task(send_reminder(e.chat_id, text, delay))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!search (.+)', func=lambda e: e.sender_id == 5457847440))
async def search_cmd(e):
    if check_cover(e): return
    q = e.pattern_match.group(1).strip()
    enc = q.replace(' ', '+')
    await e.edit(
        f"🔍 **{q}**\n\n"
        f"• [Google](https://www.google.com/search?q={enc})\n"
        f"• [DuckDuckGo](https://duckduckgo.com/?q={enc})\n"
        f"• [YouTube](https://www.youtube.com/results?search_query={enc})\n"
        f"• [Wikipedia](https://ru.wikipedia.org/wiki/Special:Search?search={enc})"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!shorten (.+)', func=lambda e: e.sender_id == 5457847440))
async def shorten_cmd(e):
    if check_cover(e): return
    url = e.pattern_match.group(1).strip()
    await e.edit("⏳ Сокращаю...")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://tinyurl.com/api-create.php?url={url}",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                short = await r.text()
        if short.startswith('http'):
            await e.edit(f"✂️ **Оригинал:** `{url[:55]}{'…' if len(url) > 55 else ''}`\n🔗 **Короткая:** {short.strip()}")
        else:
            raise Exception()
    except Exception:
        await e.edit("❌ Ошибка. Проверь URL.")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!weather (.+)', func=lambda e: e.sender_id == 5457847440))
async def weather_cmd(e):
    if check_cover(e): return
    city = e.pattern_match.group(1).strip()
    enc = city.replace(' ', '+')
    await e.edit(
        f"🌤️ **Погода: {city}**\n\n"
        f"• [wttr.in](https://wttr.in/{enc})\n"
        f"• [OpenWeatherMap](https://openweathermap.org/find?q={enc})\n"
        f"• [Weather.com](https://weather.com/ru-RU/weather/today/l/{enc})"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!translate (.+)', func=lambda e: e.sender_id == 5457847440))
async def translate_cmd(e):
    if check_cover(e): return
    text = e.pattern_match.group(1).strip()
    enc = text.replace(' ', '%20')
    await e.edit(
        f"🌐 **Перевод:** _{text}_\n\n"
        f"• [RU→EN](https://translate.google.com/?sl=ru&tl=en&text={enc})\n"
        f"• [EN→RU](https://translate.google.com/?sl=en&tl=ru&text={enc})\n"
        f"• [Auto→RU](https://translate.google.com/?sl=auto&tl=ru&text={enc})"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!base64 (encode|decode) (.+)', func=lambda e: e.sender_id == 5457847440))
async def base64_cmd(e):
    if check_cover(e): return
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
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!hash (.+)', func=lambda e: e.sender_id == 5457847440))
async def hash_cmd(e):
    if check_cover(e): return
    text = e.pattern_match.group(1).strip().encode()
    await e.edit(
        f"#️⃣ **Хэши**\n\n"
        f"MD5:    `{hashlib.md5(text).hexdigest()}`\n"
        f"SHA1:   `{hashlib.sha1(text).hexdigest()}`\n"
        f"SHA256: `{hashlib.sha256(text).hexdigest()}`\n"
        f"SHA512: `{hashlib.sha512(text).hexdigest()[:64]}…`"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!morse (.+)', func=lambda e: e.sender_id == 5457847440))
async def morse_cmd(e):
    if check_cover(e): return
    text = e.pattern_match.group(1).strip()
    await e.edit(f"📡 **Морзе:**\n_{text}_\n\n`{morse_enc(text)}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!caesar (encode|decode) (\d+) (.+)', func=lambda e: e.sender_id == 5457847440))
async def caesar_cmd(e):
    if check_cover(e): return
    mode, shift, text = e.pattern_match.group(1), int(e.pattern_match.group(2)), e.pattern_match.group(3)
    res = caesar(text, shift, dec=(mode == 'decode'))
    await e.edit(f"{'🔒' if mode == 'encode' else '🔓'} **Цезарь (сдвиг {shift}):**\n_{text}_\n\n`{res}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!vigenere (encode|decode) (\S+) (.+)', func=lambda e: e.sender_id == 5457847440))
async def vigenere_cmd(e):
    if check_cover(e): return
    mode, key, text = e.pattern_match.group(1), e.pattern_match.group(2), e.pattern_match.group(3)
    res = vigenere(text, key, dec=(mode == 'decode'))
    await e.edit(f"{'🔒' if mode == 'encode' else '🔓'} **Виженер (ключ: {key}):**\n_{text}_\n\n`{res}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!password(?:\s+(\d+))?(?:\s+(simple))?$', func=lambda e: e.sender_id == 5457847440))
async def password_cmd(e):
    if check_cover(e): return
    length = max(4, min(int(e.pattern_match.group(1) or 16), 128))
    sym = not e.pattern_match.group(2)
    pwd = gen_pwd(length, sym)
    s = "🔴 Слабый" if length < 8 else "🟡 Средний" if length < 12 else "🟢 Сильный" if length < 20 else "💎 Очень сильный"
    await e.edit(f"🔑 **Пароль ({length} симв.)**\n\n`{pwd}`\n\nСила: {s}\nСимволы: {'✅' if sym else '❌'}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!qr (.+)', func=lambda e: e.sender_id == 5457847440))
async def qr_cmd(e):
    if check_cover(e): return
    text = e.pattern_match.group(1).strip().replace(' ', '+')
    await e.edit(
        f"📱 **QR-код**\n\n"
        f"🔗 [Открыть изображение](https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={text})"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!uuid$', func=lambda e: e.sender_id == 5457847440))
async def uuid_cmd(e):
    if check_cover(e): return
    ids = [str(uuid.uuid4()) for _ in range(5)]
    out = "\n".join(f"`{u}`" for u in ids)
    await e.edit(f"🆔 **Случайные UUID v4:**\n\n{out}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!color (#[0-9a-fA-F]{6}|\d+,\d+,\d+)', func=lambda e: e.sender_id == 5457847440))
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
    s_val = 0 if mx == mn else (mx - mn) / (1 - abs(2 * l_val - 1))
    if mx == mn:
        h_val = 0
    elif mx == rf:
        h_val = 60 * ((gf - bf) / (mx - mn) % 6)
    elif mx == gf:
        h_val = 60 * ((bf - rf) / (mx - mn) + 2)
    else:
        h_val = 60 * ((rf - gf) / (mx - mn) + 4)
    await e.edit(
        f"🎨 **Цвет**\n\n"
        f"HEX: `{hex_val}`\n"
        f"RGB: `rgb({r}, {g}, {b})`\n"
        f"HSL: `hsl({h_val:.0f}°, {s_val * 100:.0f}%, {l_val * 100:.0f}%)`\n\n"
        f"🔗 [Превью](https://www.colorhexa.com/{hex_val.lstrip('#')})"
    )
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!ascii (.+)', func=lambda e: e.sender_id == 5457847440))
async def ascii_cmd(e):
    if check_cover(e): return
    text = e.pattern_match.group(1).strip()
    codes = ' '.join(str(ord(c)) for c in text)
    back = ''.join(chr(int(x)) for x in codes.split())
    await e.edit(f"🔢 **ASCII коды:**\n_{text}_\n\n`{codes}`\n\nОбратно: `{back}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!type(?:\s+(fast|slow|matrix|glitch))?\s+(.+)', func=lambda e: e.sender_id == 5457847440))
async def type_cmd(e):
    if check_cover(e): return
    mode = e.pattern_match.group(1) or 'normal'
    text = e.pattern_match.group(2).strip()
    if mode == 'fast':
        msg = await e.edit("▌")
        for i in range(0, len(text), 2):
            chunk = text[:i + 2]
            await msg.edit(chunk + ("▌" if i + 2 < len(text) else ""))
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
            if i % 2 == 0 or i == len(text) - 1:
                await msg.edit(shown + ("▌" if i < len(text) - 1 else ""))
                await asyncio.sleep(0.05)
        await msg.edit(text)
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!echo (.+)', func=lambda e: e.sender_id == 5457847440))
async def echo_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, e.pattern_match.group(1).strip())
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!say (.+)', func=lambda e: e.sender_id == 5457847440))
async def say_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, e.pattern_match.group(1).strip())
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!bold (.+)', func=lambda e: e.sender_id == 5457847440))
async def bold_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, f"**{e.pattern_match.group(1).strip()}**")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!italic (.+)', func=lambda e: e.sender_id == 5457847440))
async def italic_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, f"__{e.pattern_match.group(1).strip()}__")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!mono (.+)', func=lambda e: e.sender_id == 5457847440))
async def mono_cmd(e):
    if check_cover(e): return
    await e.delete()
    await client.send_message(e.chat_id, f"`{e.pattern_match.group(1).strip()}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!clean(?:\s+(\d+))?$', func=lambda e: e.sender_id == 5457847440))
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

@client.on(events.NewMessage(pattern=r'!purge(?:\s+(\d+))?$', func=lambda e: e.sender_id == 5457847440))
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

@client.on(events.NewMessage(pattern=r'!spam (\d+) (.+)', func=lambda e: e.sender_id == 5457847440))
async def spam_cmd(e):
    if check_cover(e): return
    count, text = int(e.pattern_match.group(1)), e.pattern_match.group(2).strip()
    await e.delete()
    for _ in range(count):
        await client.send_message(e.chat_id, text)
        await asyncio.sleep(0.35)
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!forward (-?\d+)', func=lambda e: e.sender_id == 5457847440))
async def forward_cmd(e):
    if check_cover(e): return
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на сообщение: `!forward [chat_id]`")
        return
    try:
        msg = await e.get_reply_message()
        await client.forward_messages(int(e.pattern_match.group(1)), msg)
        await e.edit(f"✅ Переслано в `{e.pattern_match.group(1)}`")
    except Exception as ex:
        await e.edit(f"❌ {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!pin$', func=lambda e: e.sender_id == 5457847440))
async def pin_cmd(e):
    if check_cover(e): return
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на сообщение")
        return
    await (await e.get_reply_message()).pin(notify=False)
    await e.delete()
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!unpin$', func=lambda e: e.sender_id == 5457847440))
async def unpin_cmd(e):
    if check_cover(e): return
    if e.reply_to_msg_id:
        await (await e.get_reply_message()).unpin()
    else:
        await client.unpin_message(e.chat_id)
    await e.delete()
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!copyall (\d+) (-?\d+)', func=lambda e: e.sender_id == 5457847440))
async def copyall_cmd(e):
    if check_cover(e): return
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
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!react (.+)', func=lambda e: e.sender_id == 5457847440))
async def react_cmd(e):
    if check_cover(e): return
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на сообщение: `!react 👍`")
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
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'^!save$', func=lambda e: e.sender_id == 5457847440))
async def save_media_cmd(e):
    if check_cover(e): return
    if not e.reply_to_msg_id:
        await e.edit("ℹ️ Ответьте на фото/видео или используйте `!save key value`")
        return
    replied = await e.get_reply_message()
    if not replied.media:
        await e.edit("❌ В ответном сообщении нет медиа.")
        return
    os.makedirs('./media', exist_ok=True)
    try:
        path = await replied.download_media('./media/')
        name = os.path.basename(path) if path else 'unknown'
        db.set_saved(f'_media_{int(time.time())}', name)
        await e.edit(f"✅ **Сохранено:** `{name}`")
        logger.info(f"Media saved: {name}")
    except Exception as ex:
        await e.edit(f"❌ Ошибка сохранения: {ex}")
        logger.error(f"Save media error: {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!save (\S+) (.+)', func=lambda e: e.sender_id == 5457847440))
async def save_cmd(e):
    if check_cover(e): return
    k, v = e.pattern_match.group(1), e.pattern_match.group(2)
    db.set_saved(k, v)
    await e.edit(f"✅ `{k}` = _{v}_")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!get (\S+)', func=lambda e: e.sender_id == 5457847440))
async def get_cmd(e):
    if check_cover(e): return
    k = e.pattern_match.group(1)
    v = db.get_saved(k)
    await e.edit(f"📦 `{k}` = _{v}_" if v else f"❌ Ключ `{k}` не найден")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!del (\S+)', func=lambda e: e.sender_id == 5457847440))
async def del_cmd(e):
    if check_cover(e): return
    k = e.pattern_match.group(1)
    v = db.get_saved(k)
    if v is not None:
        db.del_saved(k)
        await e.edit(f"🗑 Удалено: `{k}`")
    else:
        await e.edit(f"❌ `{k}` не найден")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!list$', func=lambda e: e.sender_id == 5457847440))
async def list_cmd(e):
    if check_cover(e): return
    d = db.all_saved()
    if not d:
        await e.edit("📭 Нет данных")
        db.bump_stat('cmds')
        return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v) > 40 else ''}_" for k, v in d.items())
    await e.edit(f"📦 **Сохранено ({len(d)}):**\n\n{items}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!find (.+)', func=lambda e: e.sender_id == 5457847440))
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
        await e.edit("🔍 **Ничего не найдено**")
    else:
        await e.edit(f"🔍 **Результаты поиска: {query}**\n\n" + "\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!note (\S+)(?: (.+))?', func=lambda e: e.sender_id == 5457847440))
async def note_cmd(e):
    if check_cover(e): return
    k = e.pattern_match.group(1)
    t = e.pattern_match.group(2) or ""
    if e.reply_to_msg_id:
        r = await e.get_reply_message()
        t = r.text or t
    if not t:
        await e.edit("ℹ️ `!note <название> <текст>` или ответом")
        return
    db.set_note(k, t)
    await e.edit(f"📝 Заметка сохранена: `{k}`")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!getnote (\S+)', func=lambda e: e.sender_id == 5457847440))
async def getnote_cmd(e):
    if check_cover(e): return
    k = e.pattern_match.group(1)
    v = db.get_note(k)
    if v is not None:
        await e.edit(f"📝 **{k}:**\n\n{v}")
    else:
        await e.edit(f"❌ Заметка `{k}` не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!delnote (\S+)', func=lambda e: e.sender_id == 5457847440))
async def delnote_cmd(e):
    if check_cover(e): return
    k = e.pattern_match.group(1)
    v = db.get_note(k)
    if v is not None:
        db.del_note(k)
        await e.edit(f"🗑 Заметка удалена: `{k}`")
    else:
        await e.edit(f"❌ `{k}` не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!notes$', func=lambda e: e.sender_id == 5457847440))
async def notes_cmd(e):
    if check_cover(e): return
    d = db.all_notes()
    if not d:
        await e.edit("📭 Нет заметок")
        db.bump_stat('cmds')
        return
    items = "\n".join(f"• `{k}` — _{v[:40]}{'…' if len(v) > 40 else ''}_" for k, v in d.items())
    await e.edit(f"📝 **Заметки ({len(d)}):**\n\n{items}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!todo (.+)', func=lambda e: e.sender_id == 5457847440))
async def todo_add_cmd(e):
    if check_cover(e): return
    task = e.pattern_match.group(1).strip()
    db.add_todo(task)
    todos = db.get_todos()
    await e.edit(f"✅ Задача добавлена: _{task}_\n📋 Всего: {len(todos)}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!todos$', func=lambda e: e.sender_id == 5457847440))
async def todos_cmd(e):
    if check_cover(e): return
    todos = db.get_todos()
    if not todos:
        await e.edit("📭 Список задач пуст")
        db.bump_stat('cmds')
        return
    lines = []
    for i, t in enumerate(todos, 1):
        mark = "✅" if t['done'] else "⬜"
        lines.append(f"{mark} {i}. _{t['text']}_")
    done = sum(1 for t in todos if t['done'])
    await e.edit(f"📋 **Список задач** ({done}/{len(todos)} выполнено):\n\n" + "\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!done (\d+)', func=lambda e: e.sender_id == 5457847440))
async def done_cmd(e):
    if check_cover(e): return
    idx = int(e.pattern_match.group(1)) - 1
    todos = db.get_todos()
    if 0 <= idx < len(todos):
        db.update_todo(todos[idx]['id'], done=True)
        await e.edit(f"✅ Выполнено: _{todos[idx]['text']}_")
    else:
        await e.edit(f"❌ Задача #{idx + 1} не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!undone (\d+)', func=lambda e: e.sender_id == 5457847440))
async def undone_cmd(e):
    if check_cover(e): return
    idx = int(e.pattern_match.group(1)) - 1
    todos = db.get_todos()
    if 0 <= idx < len(todos):
        db.update_todo(todos[idx]['id'], done=False)
        await e.edit(f"⬜ Снята отметка: _{todos[idx]['text']}_")
    else:
        await e.edit(f"❌ Задача #{idx + 1} не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!deltodo (\d+)', func=lambda e: e.sender_id == 5457847440))
async def deltodo_cmd(e):
    if check_cover(e): return
    idx = int(e.pattern_match.group(1)) - 1
    todos = db.get_todos()
    if 0 <= idx < len(todos):
        db.del_todo(todos[idx]['id'])
        await e.edit(f"🗑 Удалена задача: _{todos[idx]['text']}_")
    else:
        await e.edit(f"❌ Задача #{idx + 1} не найдена")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!afk(?:\s+(.+))?$', func=lambda e: e.sender_id == 5457847440))
async def afk_cmd(e):
    if check_cover(e): return
    reason = (e.pattern_match.group(1) or '').strip()
    state.set_afk(reason)
    r = f"\n📝 _{reason}_" if reason else ""
    await e.edit(f"😴 **AFK включён**{r}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!unafk$', func=lambda e: e.sender_id == 5457847440))
async def unafk_cmd(e):
    if check_cover(e): return
    dur = state.clear_afk()
    if dur is not None:
        await e.edit(f"☀️ **AFK выключен** | Отсутствовал: _{fmt_time(dur)}_")
    else:
        await e.edit("ℹ️ AFK не был включён")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!chatinfo$', func=lambda e: e.sender_id == 5457847440))
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
    await e.edit("\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!members$', func=lambda e: e.sender_id == 5457847440))
async def members_cmd(e):
    if check_cover(e): return
    try:
        p = await client.get_participants(e.chat_id)
        bots = sum(1 for x in p if x.bot)
        await e.edit(f"👥 **Участники**\n\nВсего: `{len(p)}`\n👤 Людей: `{len(p) - bots}`\n🤖 Ботов: `{bots}`")
    except Exception as ex:
        await e.edit(f"❌ {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!admins$', func=lambda e: e.sender_id == 5457847440))
async def admins_cmd(e):
    if check_cover(e): return
    try:
        admins = await client.get_participants(e.chat_id, filter=ChannelParticipantsAdmins())
        lines = [f"👑 **Администраторы ({len(admins)}):**\n"]
        for a in admins[:25]:
            name = f"{a.first_name or ''} {a.last_name or ''}".strip()
            lines.append(f"• {name} — {'@' + a.username if a.username else '`' + str(a.id) + '`'}")
        await e.edit("\n".join(lines))
    except Exception as ex:
        await e.edit(f"❌ {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!top(?:\s+(\d+))?$', func=lambda e: e.sender_id == 5457847440))
async def top_cmd(e):
    if check_cover(e): return
    limit = int(e.pattern_match.group(1) or 200)
    await e.edit("⏳ Анализирую...")
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
    await e.edit("\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!bots$', func=lambda e: e.sender_id == 5457847440))
async def bots_cmd(e):
    if check_cover(e): return
    try:
        bots = await client.get_participants(e.chat_id, filter=ChannelParticipantsBots())
        lines = [f"🤖 **Боты в чате ({len(bots)}):**\n"]
        for b in bots[:20]:
            lines.append(f"• @{b.username or b.id}")
        await e.edit("\n".join(lines))
    except Exception as ex:
        await e.edit(f"❌ {ex}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!resetdata$', func=lambda e: e.sender_id == 5457847440))
async def resetdata_cmd(e):
    if check_cover(e): return
    db.clear_all()
    state.auto_reply_enabled = False
    state.auto_reply_text = '💫 Я автоответчик, хозяин скоро ответит! Спасибо за терпение 😘'
    state.ghost_mode = False
    state.afk_start_time = None
    state.afk_reason = ''
    state._save()
    await e.edit("🧹 **Все данные сброшены.**")
    db.bump_stat('cmds')

def _resolve_format(height):
    has_ffmpeg = _detect_ffmpeg() is not None
    if not height:
        return 'bestvideo+bestaudio/best' if has_ffmpeg else 'best'
    if height <= 144:
        return 'worst'
    if has_ffmpeg:
        return f'bestvideo[height<=?{height}]+bestaudio/best[height<=?{height}]/best'
    return f'best[height<=?{height}]'


@client.on(events.NewMessage(pattern=r'!ytshow\s+(.+)', func=lambda e: e.sender_id == 5457847440))
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
        await e.edit(text)

    ydl_opts = {
        'format': _resolve_format(height),
        'outtmpl': './media/%(id)s.%(ext)s',
    }

    await edit_fn(f"⏳ Загружаю ({'авто' if not height else f'{height}p'})...")
    filename = await _run_download(edit_fn, url, ydl_opts, timeout=600)
    if filename:
        await _send_and_clean(edit_fn, e.chat_id, filename, f"🎬 YouTube: {url}")
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!dl\s+(.+)', func=lambda e: e.sender_id == 5457847440))
async def dl_cmd(e):
    if check_cover(e): return
    url = e.pattern_match.group(1).strip()

    async def edit_fn(text):
        await e.edit(text)

    ydl_opts = {
        'format': _resolve_format(None),
        'outtmpl': './media/%(id)s.%(ext)s',
    }
    await edit_fn("⏳ Универсальная загрузка...")
    try:
        filename = await _run_download(edit_fn, url, ydl_opts, timeout=600)
        if filename:
            await _send_and_clean(edit_fn, e.chat_id, filename)
    except Exception as ex:
        await e.edit(f"❌ Ошибка: {ex}")
        logger.error(f"dl error: {ex}")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!playlist\s+(.+?)(?:\s+(\d+)(?:-(\d+))?)?$', func=lambda e: e.sender_id == 5457847440))
async def playlist_cmd(e):
    if check_cover(e): return
    g = e.pattern_match
    url = g.group(1).strip()
    start_num = None
    end_num = None
    if g.group(2):
        start_num = int(g.group(2))
        end_num = int(g.group(3)) if g.group(3) else start_num

    msg = await e.edit("⏳ Получаю информацию о плейлисте...")
    try:
        ydl_opts = {
            'extract_flat': 'in_playlist',
            'quiet': True,
            'no_warnings': True,
        }
        cookies_path = _find_cookies()
        if cookies_path:
            ydl_opts['cookiefile'] = cookies_path
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        entries = info.get('entries', [])
        if not entries:
            await msg.edit("❌ Плейлист пуст или недоступен.")
            return

        total = len(entries)
        if start_num:
            s = max(1, start_num)
            e_idx = min(total, end_num) if end_num else min(total, s)
            selected = entries[s - 1:e_idx]
        else:
            selected = entries[:50]

        await msg.edit(f"📋 Плейлист: **{info.get('title', '?')}** ({len(selected)}/{total} видео)\n⏳ Начинаю загрузку...")

        for i, entry in enumerate(selected, 1):
            video_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
            vid_msg = await e.edit(f"⏳ [{i}/{len(selected)}] Загружаю: {entry.get('title', '?')}...")

            ydl_opts2 = {
                'format': _resolve_format(None),
                'outtmpl': './media/%(id)s.%(ext)s',
            }

            async def edit_vid(text, vid_msg=vid_msg):
                try:
                    await vid_msg.edit(text)
                except Exception:
                    pass

            filename = await _run_download(edit_vid, video_url, ydl_opts2, timeout=600)
            if filename:
                await _send_and_clean(edit_vid, e.chat_id, filename, f"🎬 [{i}/{len(selected)}] {entry.get('title', '')}")
                await asyncio.sleep(2)

        await e.edit(f"✅ **Плейлист загружен!** ({len(selected)}/{total} видео)")
    except Exception as ex:
        await e.edit(f"❌ Ошибка плейлиста: {ex}")
        logger.error(f"playlist error: {ex}")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!audio\s+(.+?)(?:\s+(mp3|m4a|opus))?$', func=lambda e: e.sender_id == 5457847440))
async def audio_cmd(e):
    if check_cover(e): return
    g = e.pattern_match
    url = g.group(1).strip()
    fmt = (g.group(2) or 'mp3').lower()

    async def edit_fn(text):
        await e.edit(text)

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f'./media/%(id)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': fmt,
            'preferredquality': '192',
        }],
    }
    await edit_fn(f"⏳ Извлекаю аудио ({fmt.upper()})...")
    try:
        filename = await _run_download(edit_fn, url, ydl_opts, timeout=600)
        if filename:
            audio_file = filename.rsplit('.', 1)[0] + '.' + fmt
            if os.path.exists(audio_file):
                await _send_and_clean(edit_fn, e.chat_id, audio_file, f"🎵 Аудио ({fmt.upper()})")
            elif filename and os.path.exists(filename):
                await _send_and_clean(edit_fn, e.chat_id, filename, f"🎵 Аудио ({fmt.upper()})")
            else:
                await e.edit("❌ Файл не найден после конвертации.")
    except Exception as ex:
        await e.edit(f"❌ Ошибка: {ex}")
        logger.error(f"audio error: {ex}")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!sub\s+(.+?)(?:\s+(\w{2}))?$', func=lambda e: e.sender_id == 5457847440))
async def sub_cmd(e):
    if check_cover(e): return
    g = e.pattern_match
    url = g.group(1).strip()
    lang = (g.group(2) or 'ru').lower()

    msg = await e.edit(f"⏳ Ищу субтитры ({lang})...")
    try:
        ydl_opts = {
            'writesubtitles': True,
            'subtitleslangs': [lang],
            'skip_download': True,
            'outtmpl': './media/%(id)s',
            'quiet': True,
            'no_warnings': True,
        }
        cookies_path = _find_cookies()
        if cookies_path:
            ydl_opts['cookiefile'] = cookies_path
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        sub_file = None
        for f in os.listdir('./media'):
            if f.endswith('.vtt') or f.endswith('.srt') or f.endswith('.ass'):
                if info.get('id', '') in f:
                    sub_file = os.path.join('./media', f)
                    break

        if sub_file:
            await msg.edit(f"📤 Отправляю субтитры ({lang})...")
            await client.send_file(e.chat_id, sub_file, caption=f"📝 Субтитры ({lang})")
            await asyncio.sleep(3)
            os.remove(sub_file)
            await msg.edit(f"✅ Субтитры ({lang}) отправлены.")
        else:
            await msg.edit(f"❌ Субтитры ({lang}) не найдены для этого видео.")
    except Exception as ex:
        await msg.edit(f"❌ Ошибка: {ex}")
        logger.error(f"sub error: {ex}")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!watch\s+(on|off)$', func=lambda e: e.sender_id == 5457847440))
async def watch_cmd(e):
    global _watch_task
    arg = e.pattern_match.group(1)
    if arg == 'on':
        if _watch_task and not _watch_task.done():
            await e.edit("⚠️ Мониторинг уже запущен.")
            return
        db.clear_sessions()
        try:
            result = await client(GetAuthorizationsRequest())
            for auth in result.authorizations:
                h = hashlib.md5(f"{auth.hash}{auth.device_model}{auth.platform}".encode()).hexdigest()
                db.save_session(h, f'{{"device":"{auth.device_model}","platform":"{auth.platform}","ip":"{auth.ip}","date":"{auth.date_created}"}}')
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
                            db.save_session(h, f'{{"device":"{auth.device_model}","platform":"{auth.platform}","ip":"{auth.ip}","date":"{auth.date_created}"}}')
                            me = await client.get_me()
                            await client.send_message(me.id, f"⚠️ **Новый вход**\nУстройство: {auth.device_model}\nПлатформа: {auth.platform}\nIP: {auth.ip}\nДата: {auth.date_created}")
                            logger.warning(f"New session: {auth.device_model} {auth.ip}")
                except Exception as ex:
                    logger.error(f"Watch error: {ex}")
                await asyncio.sleep(300)

        _watch_task = asyncio.create_task(monitor())
        await e.edit("👁️ **Мониторинг сессий ВКЛЮЧЁН.** Проверка каждые 5 мин.")
    else:
        if _watch_task and not _watch_task.done():
            _watch_task.cancel()
            _watch_task = None
        await e.edit("👁️ **Мониторинг сессий ВЫКЛЮЧЕН.**")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!check_email\s+(\S+)', func=lambda e: e.sender_id == 5457847440))
async def check_email_cmd(e):
    if check_cover(e): return
    email = e.pattern_match.group(1).strip().lower()
    msg = await e.edit(f"🔍 Проверяю {email}...")
    try:
        headers = {'hibp-api-key': '', 'User-Agent': 'TelegramUserBot/1.0'}
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


@client.on(events.NewMessage(pattern=r'!protect\s+(on|off)$', func=lambda e: e.sender_id == 5457847440))
async def protect_cmd(e):
    global _protect_task
    arg = e.pattern_match.group(1)
    if arg == 'on':
        if _protect_task and not _protect_task.done():
            await e.edit("⚠️ Защита уже включена.")
            return
        dialogs = await client.get_dialogs()
        db.clear_protected_chats()
        for d in dialogs:
            db.add_protected_chat(d.id)
        await e.edit(f"🔒 **Защита ВКЛЮЧЕНА.** Отслеживается {len(dialogs)} чатов.")

        async def monitor():
            while True:
                try:
                    current = {d.id for d in await client.get_dialogs()}
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
        await e.edit("🔓 **Защита ВЫКЛЮЧЕНА.**")
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

    if state.mute_enabled:
        logger.info(f"🔇 Mute — игнорирую {uid}")
        return

    if state.lock_enabled:
        try:
            me = await client.get_me()
            if uid == me.id:
                pass
            else:
                is_contact = False
                try:
                    contact = await client.get_entity(uid)
                    is_contact = getattr(contact, 'contact', False)
                except Exception:
                    pass
                if not is_contact:
                    common = await client.get_common_chats(uid)
                    if not common:
                        logger.info(f"🔒 Lock: {uid} не контакт и нет общих чатов — игнорирую")
                        return
        except Exception as ex:
            logger.warning(f"Lock check error for {uid}: {ex}")

    silent_mode = state.silent_enabled

    if event.reply_to_msg_id:
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
                    if custom_reply:
                        reply_text = custom_reply
                    else:
                        reply_text = get_rp_reply(cmd_part)

                    sent = await event.reply(f"{action_text}\n{reply_text}")
                    logger.info(f"✅ RP: {user_name} -> {target_name} ({cmd_part}) | реплика: {'кастом' if custom_reply else 'рандом'}")

                    if state.shadow_enabled:
                        asyncio.create_task(shadow_delete_msg(sent))

                    db.bump_stat('cmds')
                    return
        except Exception as e:
            logger.error(f"Ошибка RP: {e}")

    if state.afk_start_time and now - reply_cooldown.get(f'afk_{uid}', 0) > 60:
        if silent_mode:
            logger.info(f"🔇 Silent — AFK ответ скрыт для {uid}")
        else:
            dur = fmt_time(now - state.afk_start_time)
            reason_part = f"\n📝 _{state.afk_reason}_" if state.afk_reason else ""
            reply_cooldown[f'afk_{uid}'] = now
            sent = await event.reply(f"😴 Хозяин AFK уже **{dur}**{reason_part}")
            if state.shadow_enabled:
                asyncio.create_task(shadow_delete_msg(sent))

    if state.auto_reply_enabled and now - reply_cooldown.get(uid, 0) > 10:
        reply_text = db.get_reply_text(uid)
        if reply_text is None:
            reply_text = db.get_default_reply()
        if reply_text is None:
            reply_text = state.auto_reply_text if state.auto_reply_text else None
        if reply_text and not silent_mode:
            reply_cooldown[uid] = now
            await asyncio.sleep(1)
            sent = await event.reply(reply_text)
            if state.shadow_enabled:
                asyncio.create_task(shadow_delete_msg(sent))
        elif silent_mode:
            logger.info(f"🔇 Silent — автоответ скрыт для {uid}")

async def shadow_delete_msg(msg, delay=None):
    d = delay or state.shadow_delay or 5
    await asyncio.sleep(d)
    try:
        await msg.delete()
    except Exception:
        pass

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
    'ytshow':       {'desc': 'Скачать и отправить видео с YouTube', 'syntax': '!ytshow <URL> [качество]', 'example': '!ytshow https://youtu.be/... 720'},
    'dl':           {'desc': 'Универсальная загрузка', 'syntax': '!dl <URL>', 'example': '!dl https://www.tiktok.com/@user/video/123'},
    'playlist':     {'desc': 'Скачать плейлист', 'syntax': '!playlist <URL> [кол-во] | [start-end]', 'example': '!playlist https://youtube.com/playlist?list=... 5'},
    'audio':        {'desc': 'Скачать аудио (MP3/M4A/OPUS)', 'syntax': '!audio <URL> [формат]', 'example': '!audio https://youtu.be/... mp3'},
    'sub':          {'desc': 'Скачать субтитры', 'syntax': '!sub <URL> [язык]', 'example': '!sub https://youtu.be/... ru'},
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
        '!watch', '!check_email', '!protect'
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
        "`!ytshow <URL> [качество]` — скачать и отправить видео с YouTube\n"
        "`!dl <URL>` — универсальная загрузка (YouTube, TikTok, Instagram и др.)\n"
        "`!playlist <URL> [кол-во | start-end]` — загрузка плейлиста (до 50)\n"
        "`!audio <URL> [mp3|m4a|opus]` — извлечение аудио\n"
        "`!sub <URL> [ru|en]` — скачать субтитры в SRT\n\n"
        "💡 Прогресс обновляется каждые 5 сек. Таймаут — 10 мин."
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
        "**Категории:**\n"
        f"{chr(10).join(f'• {cat.capitalize()}: {', '.join(get_category_commands(cat))}' for cat in get_all_categories())}"
    ),
}

@client.on(events.NewMessage(pattern=r'!help(?:\s+(.+))?$', func=lambda e: e.sender_id == 5457847440))
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
            await e.edit("\n".join(lines))
            db.bump_stat('cmds')
            return
        else:
            await e.edit(f"❌ Команда `{cmd_name}` не найдена.\n💡 `!help cmd <команда>`")
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
        await e.edit(msg)
        db.bump_stat('cmds')
        return

    if arg:
        cat = arg
        if cat not in HELP_CATS:
            cats = ', '.join(f"`!help {c}`" for c in HELP_CATS)
            await e.edit(f"❌ Категория `{cat}` не найдена.\n\nДоступные категории:\n{cats}")
            db.bump_stat('cmds')
            return
        text = HELP_CATS[cat]
        cmds = COMMANDS_LIST.get(cat, [])
        if cmds:
            text += "\n\n📋 **Команды для копирования:**\n" + ", ".join(f"`{cmd}`" for cmd in cmds)
        text += "\n\n💡 Для справки по команде: `!help cmd <команда>`\n💡 Для всех команд: `!help all`"
        await e.edit(text)
        db.bump_stat('cmds')
        return

    lines = ["📚 **UserBot Help**\n\nВыбери категорию — скопируй команду и отправь:\n"]
    for cat_name in HELP_CATS:
        emoji = EMOJI_MAP.get(cat_name, '•')
        lines.append(f"{emoji} `{cat_name.capitalize()}` → `!help {cat_name}`")
    lines.append("\n💡 Для справки по команде: `!help cmd <команда>`")
    lines.append("💡 Для всех команд: `!help all`")
    await e.edit("\n".join(lines))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!commands$', func=lambda e: e.sender_id == 5457847440))
async def commands_cmd(e):
    if check_cover(e): return
    await e.edit("ℹ️ Используйте `!help all` для списка всех команд с описанием.")
    db.bump_stat('cmds')

if __name__ == "__main__":
    print("🚀 Запуск UserBot (с RP-командами)...")
    os.makedirs('./media', exist_ok=True)
    Thread(target=run_web, daemon=True).start()
    client.start()
    print("✅ Бот запущен! Логи в консоли.")
    client.run_until_disconnected()
