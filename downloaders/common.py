import asyncio
import base64
import contextvars
import glob
import os
import re
import subprocess
import sys
import time

import aiohttp
import yt_dlp

from config import BASE_DIR, MEDIA_DIR, logger, PROXY, PROXY_LIST
from utils import progress_bar

_proxy_list = list(PROXY_LIST)
_single_proxy = PROXY
_last_good_proxy = None   # рабочий прокси — ставим первым кандидатом


def _proxy_candidates():
    proxies = [_p for _p in _proxy_list if _p] or ([_single_proxy] if _single_proxy else [])
    if proxies and _last_good_proxy in proxies:
        proxies = [_last_good_proxy] + [p for p in proxies if p != _last_good_proxy]
    if proxies:
        return proxies + [None]
    return [None]


def _with_proxy(opts, proxy):
    if proxy:
        o = dict(opts)
        o['proxy'] = proxy
        return o
    return opts


def _dl_rotate(sync_fn):
    global _last_good_proxy
    last_ex = None
    for proxy in _proxy_candidates():
        label = proxy or 'прямое подключение'
        try:
            result = sync_fn(proxy)
            _last_good_proxy = proxy   # запоминаем победителя: следующий раз — первым
            return result
        except Exception as ex:
            last_ex = ex
            logger.warning(f"Загрузка через {label} не удалась: {ex}")
            continue
    if last_ex:
        raise last_ex
    raise ValueError("Не удалось скачать: нет рабочих прокси")

# cookies.txt лежит в корне проекта, а не рядом с пакетом (__file__ теперь downloaders/common.py)
_COOKIES_PATH = os.path.join(BASE_DIR, 'cookies.txt')

def _cookies_from_env():
    """Вернуть (содержимое, из env) без записи на диск: импорт не должен трогать файлы."""
    env_content = os.environ.get('YOUTUBE_COOKIES') or ''
    if env_content.strip():
        return env_content.strip() + '\n', True
    b64 = os.environ.get('COOKIES_B64') or ''
    if b64.strip():
        try:
            decoded = base64.b64decode(b64).decode('utf-8', errors='ignore')
            return decoded.strip() + '\n', True
        except Exception as ex:
            logger.warning(f"Ошибка декодирования COOKIES_B64: {ex}")
    return None, False


def _cookies_content():
    content, _ = _cookies_from_env()
    if content is not None:
        return content
    if os.path.exists(_COOKIES_PATH):
        with open(_COOKIES_PATH, encoding='utf-8', errors='ignore') as f:
            return f.read()
    return ''


def _ensure_cookies_from_env():
    """Записать cookies.txt из env перед реальной загрузкой, а не при импорте."""
    content, from_env = _cookies_from_env()
    if not from_env:
        return False
    try:
        current = None
        if os.path.exists(_COOKIES_PATH):
            with open(_COOKIES_PATH, encoding='utf-8', errors='ignore') as f:
                current = f.read()
        if current != content:
            with open(_COOKIES_PATH, 'w', encoding='utf-8') as f:
                f.write(content)
        logger.info("cookies.txt подготовлен из переменных окружения")
        return True
    except OSError as ex:
        logger.warning(f"Не удалось записать cookies.txt: {ex}")
        return False


_COOKIES_VALID = False
_AUTH_COOKIE_KEYS = ('SID', 'SSID', 'HSID', 'APISID', 'SAPISID', '__Secure-1PAPISID',
                     '__Secure-3PAPISID', '__Secure-3PSID', 'LOGIN_INFO', 'LOGIN')
if _cookies_content():
    content = _cookies_content()
    if '.youtube.com' in content and ('\tTRUE\t' in content or 'cookie' in content.lower()):
        real_auth = [k for k in _AUTH_COOKIE_KEYS
                     if re.search(r'\t' + re.escape(k) + r'\t', content)]
        _COOKIES_VALID = bool(real_auth)
        lines = [l for l in content.splitlines() if l.strip() and not l.startswith('#')]
        logger.info(f"cookies.txt: {len(lines)} строк, домен youtube — "
                    f"{'OK, авторизация (' + ', '.join(real_auth[:3]) + ')' if real_auth else 'есть, но авторизующие куки НЕ найдены'}")
        if not real_auth and 'abc123' in content:
            logger.warning("cookies.txt похож на шаблон-пример из cookies.txt.example — "
                           "нужен реальный экспорт из браузера")
    else:
        logger.warning("cookies.txt существует, но похож на неверный формат (нужен Netscape)")
else:
    logger.info("cookies.txt не найден — скачивание YouTube может не работать без авторизации")
    logger.info("Задай YOUTUBE_COOKIES в .env или создай cookies.txt из cookies.txt.example")

# ── Проверки окружения — по требованию, а не на импорте ──
# Раньше здесь же лежали subprocess-вызовы ffmpeg/node и (при UPDATE_YTDLP=1)
# pip install: `import downloaders` тянул всё это до подключения к Telegram,
# поэтому без ffmpeg/node не стартовало вообще ничего, а импорт в тестах
# и в utils/core платил за чужие зависимости.
_runtime_ready = False


def _ensure_runtime():
    """Ленивая инициализация: JS-рантаймы, ffmpeg, версия и обновление yt-dlp.
    Идемпотентна; вызывается на первой реальной загрузке."""
    global _runtime_ready, _HAS_FFMPEG
    if _runtime_ready:
        return
    _runtime_ready = True
    _ensure_cookies_from_env()
    if os.environ.get('UPDATE_YTDLP', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        _update_ytdlp()
    try:
        logger.info(f"yt-dlp version: {yt_dlp.version.__version__}")
    except Exception:
        logger.info("yt-dlp version: unknown")
    for _rt in ('node', 'deno'):
        try:
            r = subprocess.run([_rt, '--version'], capture_output=True, check=False, timeout=5)
            if r.returncode == 0:
                _JS_RUNTIMES[_rt] = {}
                logger.info(f"JS runtime: {_rt} найден")
        except (FileNotFoundError, OSError):
            pass
    if not _JS_RUNTIMES:
        logger.warning("JS runtime не найден (node/deno) — установи для лучшей совместимости с YouTube")
        logger.warning("  apt install nodejs  или  curl -fsSL https://deno.land/install.sh | sh")
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        _HAS_FFMPEG = True
    except Exception:
        pass


def _update_ytdlp():
    try:
        old_ver = getattr(yt_dlp.version, '__version__', '???')
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--upgrade', '--quiet', 'yt-dlp'],
            capture_output=True, timeout=60
        )
        result = subprocess.run(
            [sys.executable, '-m', 'yt_dlp', '--version'],
            capture_output=True, text=True, timeout=30
        )
        new_ver = result.stdout.strip() or '???'
        if old_ver != new_ver:
            logger.info(f"yt-dlp обновлён: {old_ver} → {new_ver}")
        else:
            logger.info(f"yt-dlp {old_ver} (актуальная)")
    except Exception as ex:
        logger.warning(f"yt-dlp auto-update failed: {ex}")

_JS_RUNTIMES = {}
_HAS_FFMPEG = False

_YT_DL_OPTS = {
    'outtmpl': os.path.join(MEDIA_DIR, '%(id)s.%(ext)s'),
    'quiet': True,
    'no_warnings': True,
    'noplaylist': True,
    'playlist_items': '1',
    'extractor_retries': 5,
    'fragment_retries': 5,
    'retry_sleep': lambda n: 5 + n * 3,
    'throttledratelimit': 100000,
    'sleep_interval_requests': 2,
    'js_runtimes': _JS_RUNTIMES,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
    },
}


def _yt_opts():
    """Копия базовых опций yt-dlp после инициализации окружения.
    js_runtimes — тот же dict (_ensure_runtime мутирует его), поэтому опции,
    созданные до первой загрузки, тоже видят найденный рантайм."""
    _ensure_runtime()
    return dict(_YT_DL_OPTS)


_AUTH_WALL_HINTS = (
    re.compile(r'sign\s?in\s?to\s?confirm', re.I),
    re.compile(r'confirm you\'?re not a bot', re.I),
    re.compile(r'requires a Google account', re.I),
    re.compile(r'log\s*in\s*(is|be|to)', re.I),
    re.compile(r'required to purchase', re.I),
    re.compile(r'Insufficient permissions', re.I),
    re.compile(r'private video', re.I),
    re.compile(r'HTTP Error 403', re.I),
    re.compile(r'Video unavailable', re.I),
    re.compile(r'авторизац', re.I),
    re.compile(r'требуется вход', re.I),
    re.compile(r'не прошли проверку', re.I),
    re.compile(r'блокирует (?:IP|сервер)', re.I),
)


def _is_auth_wall(ex):
    text = str(ex)
    return any(p.search(text) for p in _AUTH_WALL_HINTS)

_YT_CLIENT_FALLBACKS = [
    {'youtube': {'player_client': ['android']}},
    {'youtube': {'player_client': ['mweb'], 'player_skip': ['hls', 'js']}},
]

_max_file_size = 1500


def set_max_file_size(mb: int):
    global _max_file_size
    _max_file_size = mb


def get_max_file_size():
    """Актуальный лимит (импорт по значению замораживал бы его на 1500 МБ)."""
    return _max_file_size


def has_ffmpeg():
    """Есть ли ffmpeg; после _ensure_runtime() значение может измениться —
    поэтому функция, а не импортированная константа."""
    return _HAS_FFMPEG


# ── Прогресс скачивания: хук yt-dlp (из потока) + async-репортёр в статус-сообщение ──

class _DownloadProgress:
    def __init__(self):
        self.percent = 0.0
        self.speed = ''
        self.eta = ''
        self.updated = time.time()
        self.active = True

    def hook(self, d):
        """progress_hook yt-dlp — вызывается из рабочего потока."""
        st = d.get('status')
        if st == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            done = d.get('downloaded_bytes') or 0
            if total:
                self.percent = min(100.0, done * 100.0 / total)
            self.speed = (d.get('_speed_str') or '').strip()
            self.eta = (d.get('_eta_str') or '').strip()
        elif st == 'finished':
            self.percent = 100.0
            self.speed = ''
            self.eta = ''
        self.updated = time.time()

    def stop(self):
        self.active = False


# Прогресс конкретной загрузки. ContextVar, а не модульный глобал: при
# параллельных загрузках (разные платформы) глобал перезаписывался, и бар
# с процентами уезжал в чужое сообщение (asyncio.to_thread копирует контекст).
_current_progress = contextvars.ContextVar('dl_progress', default=None)


class _ProgressYDL(yt_dlp.YoutubeDL):
    """YoutubeDL с хуком текущего прогресса — глобальная замена yt_dlp.YoutubeDL,
    чтобы проценты шли из всех путей (YouTube, generic, TikTok, Instagram…)."""

    def __init__(self, opts=None, *args, **kwargs):
        o = dict(opts or {})
        prog = _current_progress.get()
        if prog is not None:
            hooks = list(o.get('progress_hooks') or [])
            hooks.append(prog.hook)
            o['progress_hooks'] = hooks
        super().__init__(o, *args, **kwargs)


yt_dlp.YoutubeDL = _ProgressYDL


async def _report_progress(prog, edit_fn, label="📥 Скачиваю:"):
    """Рисует `[бар] N%  ⚡скорость ⏳ETA` в статус-сообщение раз в ~2.5с."""
    last_shown = -1.0
    while prog.active:
        await asyncio.sleep(2.5)
        if not prog.active:
            break
        if time.time() - prog.updated > 15:
            continue   # yt-dlp молчит (постобработка/слияние) — не мешаем
        p = prog.percent
        if p < 1 or p == last_shown:
            continue
        last_shown = p
        bar = progress_bar(p, 100, 10)
        tail = f" ⚡{prog.speed}" if prog.speed else ''
        tail += f" ⏳{prog.eta}" if prog.eta else ''
        try:
            await edit_fn(f"{label} [{bar}] **{p:.0f}%**{tail}")
        except Exception:
            pass


_INVIDIOUS_INSTANCES = [
    'inv.nadeko.net',
    'yewtu.be',
    'invidious.snopyta.org',
    'inv.vern.cc',
    'vid.puffyan.us',
]


def _yt_id(url):
    m = re.search(r'(?:v=|youtu\.be/|list=)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else None


async def _try_invidious(url, mode):
    video_id = _yt_id(url)
    if not video_id:
        return None
    os.makedirs(MEDIA_DIR, exist_ok=True)
    for instance in _INVIDIOUS_INSTANCES:
        try:
            api_url = f"https://{instance}/api/v1/videos/{video_id}"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.get(api_url) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
            title = data.get('title', video_id) or video_id
            combined = data.get('formatStreams') or []
            adaptive = data.get('adaptiveFormats') or []
            if mode == 'audio':
                # Только аудио-дорожки: video-only adaptive-форматы имеют
                # больший bitrate, иначе max() вернул бы видео вместо аудио
                audio_picks = [f for f in adaptive
                               if str(f.get('type') or '').lower().startswith('audio')]
                best = max((f for f in audio_picks if f.get('url') and (f.get('bitrate') or 0)),
                           key=lambda f: f['bitrate'], default=None)
            else:
                best = max((f for f in combined if f.get('url') and (f.get('height') or 0)), key=lambda f: f['height'], default=None)
                if not best and adaptive:
                    best = max((f for f in adaptive if f.get('url') and (f.get('height') or 0)), key=lambda f: f['height'], default=None)
            if not best:
                continue
            ext = best.get('container', 'mp4')
            path = os.path.join(MEDIA_DIR, f"{video_id}_invidious.{ext}")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
                async with s.get(best['url']) as resp:
                    if resp.status != 200:
                        continue
                    with open(path, 'wb') as f:
                        while True:
                            chunk = await resp.content.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                logger.info(f"Invidious download OK: {title} ({instance})")
                return path
        except Exception as ex:
            logger.warning(f"Invidious {instance} failed: {ex}")
            continue
    return None


def _resolve_yt_path(ydl, info):
    filepath = None
    if info.get('requested_downloads'):
        filepath = info['requested_downloads'][0].get('filepath')
    if not filepath or not os.path.exists(filepath or ''):
        filepath = ydl.prepare_filename(info)
    if filepath and os.path.exists(filepath):
        return filepath
    video_id = info.get('id')
    if video_id:
        pattern = os.path.join(MEDIA_DIR, f'{video_id}.*')
        matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        if matches:
            return matches[0]
    raise ValueError("Файл не найден после загрузки (возможно, проблема с ffmpeg пост-обработкой)")


def _probe_formats(url, opts):
    probe_opts = dict(opts)
    probe_opts.pop('format', None)
    probe_opts['skip_download'] = True
    try:
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            logger.info(f"YouTube probe: id={info.get('id', '?')}, "
                        f"title=\"{info.get('title', '?')[:80]}\", "
                        f"channel={info.get('channel', '?')}")
            if info.get('title') or info.get('entries'):
                return info
            logger.warning("Probe вернул пустой результат (возможно, требуется авторизация)")
    except yt_dlp.utils.DownloadError as ex:
        logger.warning(f"Probe failed: {ex}")
    except Exception as ex:
        logger.warning(f"Probe unexpected: {ex}")
    _dl_diag(url)
    return None


def _dl_diag(url):
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'yt_dlp', '--verbose', '--flat-playlist', '-J', '--no-check-certificates', url],
            capture_output=True, text=True, timeout=60
        )
        logger.info(f"yt-dlp CLI stdout: {result.stdout[:500]}")
        logger.info(f"yt-dlp CLI stderr: {result.stderr[:500]}")
    except Exception as ex:
        logger.error(f"yt-dlp CLI diag failed: {ex}")


def _cli_download(url, output_path, is_audio=False):
    cmd = [
        sys.executable, '-m', 'yt_dlp',
        '-o', output_path,
        '--no-playlist',
        '--no-check-certificates',
    ]
    if is_audio:
        # Без -f CLI скачал бы дефолтное видео; m4a предпочитаем — Telegram
        # отправляет её как аудио (webm по mime video/webm показывается как видео)
        cmd += ['-f', 'bestaudio[ext=m4a]/bestaudio']
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            matches = sorted(glob.glob(output_path.replace('%(ext)s', '*')), key=os.path.getmtime, reverse=True)
            if matches:
                return matches[0]
            m = re.search(r'\[download\]\s+(.+?)\s+has already been downloaded', result.stderr, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        logger.error(f"CLI download failed (rc={result.returncode}): {result.stderr[:3000]}")
        return None
    except subprocess.TimeoutExpired:
        logger.error("CLI download timed out")
        return None
    except Exception as ex:
        logger.error(f"CLI download error: {ex}")
        return None


def _auth_final_error(last_ex):
    hint = (
        "YouTube требует авторизацию и блокирует сервер. Варианты:\n"
        "  1) Обнови cookies (YOUTUBE_COOKIES в .env, формат Netscape)\n"
        "  2) Укажи рабочий прокси: PROXY=http://лог:пароль@хост:порт или\n"
        "     PROXY_LIST=ip1,ip2,socks5://... (см. .env.example)\n"
        "  3) Смени хостинг / используй домашний IP"
    )
    return f"YouTube: {last_ex}\n\n🔑 {hint}"


def _pick_format_and_download(url, opts, quality, is_audio):
    last_ex = None

    normal_attempts = [opts]
    if _COOKIES_VALID:
        with_cookie = dict(opts)
        with_cookie['cookiefile'] = _COOKIES_PATH
        normal_attempts.append(with_cookie)

    for o in normal_attempts:
        try:
            return _download_with_opts(url, o, quality, is_audio)
        except (yt_dlp.utils.DownloadError, ValueError) as ex:
            last_ex = ex
            logger.warning(f"Попытка не удалась: {ex}")
            if not _is_auth_wall(ex):
                raise

    logger.warning("Стена авторизации: пробую обходные клиенты YouTube (android/mweb)...")
    for fallback in _YT_CLIENT_FALLBACKS:
        client_attempts = []
        o = dict(opts)
        o['extractor_args'] = fallback
        o['socket_timeout'] = 45
        o['extractor_retries'] = 10
        o['fragment_retries'] = 10
        client_attempts.append(o)
        if _COOKIES_VALID:
            oc = dict(o)
            oc['cookiefile'] = _COOKIES_PATH
            client_attempts.append(oc)
        for oa in client_attempts:
            try:
                result = _download_with_opts(url, oa, quality, is_audio)
                logger.info(f"Обходной клиент {fallback['youtube']['player_client']} сработал")
                return result
            except (yt_dlp.utils.DownloadError, ValueError) as ex:
                last_ex = ex
                logger.warning(f"Обходной клиент {fallback['youtube']['player_client']} не сработал: {ex}")

    raise ValueError(_auth_final_error(last_ex))


def _download_with_opts(url, opts, quality, is_audio):
    os.makedirs(MEDIA_DIR, exist_ok=True)

    if not _HAS_FFMPEG:
        probe = _probe_formats(url, opts)
        if probe is None:
            logger.warning("Python API probe failed, trying CLI fallback...")
            # %(ext)s, а не жёсткий ext: литерал '.mp3' в outtmpl давал
            # видеофайл с именем .mp3
            cli_path = os.path.join(MEDIA_DIR, '%(id)s.%(ext)s')
            cli_result = _cli_download(url, cli_path, is_audio=is_audio)
            if cli_result:
                return cli_result
            if not _JS_RUNTIMES:
                hint = (
                    "\n\n📦 Установи JS-рантайм для yt-dlp и перезапусти:\n"
                    "  apt install nodejs\n"
                    "  # или: curl -fsSL https://deno.land/install.sh | sh"
                )
            else:
                hint = (
                    "\n\n🔑 YouTube всё ещё блокирует IP. Тогда нужны cookies:\n"
                    "  cookies.txt.example → cookies.txt, экспорт из браузера"
                )
            raise ValueError("YouTube блокирует сервер." + hint)

    if _HAS_FFMPEG and is_audio:
        opts['format'] = 'ba/b'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': str(quality or 192),
        }]
    elif _HAS_FFMPEG and not is_audio:
        opts['format'] = 'bv*+ba/b'
        if quality:
            opts['format'] = f'bv*[height<={quality}]+ba/b[height<={quality}]'
        opts['merge_output_format'] = 'mp4'
    else:
        # Аудио без ffmpeg: только аудио-потоки, БЕЗ отката на 'b' (best = видео) —
        # иначе вместо аудио молча уходит видео. m4a предпочтительнее: webm/opus
        # Telegram по mime video/webm показывает как видео.
        formats_to_try = ['bestaudio[ext=m4a]/bestaudio'] if is_audio else ['b', 'bv']
        last_ex = None
        for fmt in formats_to_try:
            opts['format'] = fmt
            try:
                logger.info(f'yt-dlp opts: format={opts.get("format")}, ffmpeg={_HAS_FFMPEG}')
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return _resolve_yt_path(ydl, info)
            except yt_dlp.utils.DownloadError as ex:
                last_ex = ex
                continue
        if is_audio:
            raise ValueError(
                f"YouTube: аудио-поток недоступен ({last_ex}). "
                "Установи ffmpeg — тогда аудио качается любым форматом и конвертируется в mp3."
            )
        raise ValueError(f"YouTube: {last_ex}")

    logger.info(f'yt-dlp opts: format={opts.get("format")}, ffmpeg={_HAS_FFMPEG}')
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return _resolve_yt_path(ydl, info)


