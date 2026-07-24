import asyncio
import glob
import os
import re
import subprocess
import sys

import yt_dlp

from config import MEDIA_DIR, logger
from utils import format_bytes

_COOKIES_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), 'cookies.txt'))
_COOKIES_VALID = False
if os.path.exists(_COOKIES_PATH):
    with open(_COOKIES_PATH, encoding='utf-8', errors='ignore') as f:
        content = f.read()
    if '.youtube.com' in content and ('\tTRUE\t' in content or 'cookie' in content.lower()):
        _COOKIES_VALID = True
        logger.info(f"cookies.txt: {len(content)} байт, формат OK")
    else:
        logger.warning("cookies.txt существует, но похож на неверный формат (нужен Netscape)")
else:
    logger.warning("cookies.txt не найден — YouTube может требовать авторизации")

try:
    logger.info(f"yt-dlp version: {yt_dlp.version.__version__}")
except Exception:
    logger.info("yt-dlp version: unknown")

_YT_DL_OPTS = {
    'outtmpl': os.path.join(MEDIA_DIR, '%(id)s.%(ext)s'),
    'quiet': True,
    'no_warnings': True,
    'noplaylist': True,
    'extractor_retries': 5,
    'fragment_retries': 5,
    'retry_sleep': lambda n: 5 + n * 3,
    'throttledratelimit': 100000,
    'sleep_interval_requests': 2,
    'cookiefile': _COOKIES_PATH if _COOKIES_VALID else None,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
    },
}

_HAS_FFMPEG = False
try:
    subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    _HAS_FFMPEG = True
except Exception:
    pass

_max_file_size = 1500


def set_max_file_size(mb: int):
    global _max_file_size
    _max_file_size = mb


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
    attempts = [
        ('flat', {'extract_flat': True}),
        ('web_client', {'extract_flat': True,
                        'extractor_args': {'youtube': {'player_client': ['web']}}}),
    ]
    for label, extra in attempts:
        probe_opts = dict(opts)
        probe_opts.pop('format', None)
        probe_opts.pop('extractor_args', None)
        probe_opts['skip_download'] = True
        probe_opts.update(extra)
        try:
            with yt_dlp.YoutubeDL(probe_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                logger.info(f"YouTube probe [{label}]: id={info.get('id', '?')}, "
                            f"title=\"{info.get('title', '?')[:80]}\", "
                            f"channel={info.get('channel', '?')}")
                if info.get('title'):
                    return info
        except yt_dlp.utils.DownloadError as ex:
            logger.warning(f"Probe [{label}] failed: {ex}")
            continue
        except Exception as ex:
            logger.warning(f"Probe [{label}] unexpected: {ex}")
            continue
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


def _cli_download(url, output_path):
    cmd = [
        sys.executable, '-m', 'yt_dlp',
        '-f', 'best',
        '-o', output_path,
        '--no-playlist',
        '--no-check-certificates',
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            matches = sorted(glob.glob(output_path.replace('%(ext)s', '*')), key=os.path.getmtime, reverse=True)
            if matches:
                return matches[0]
            stderr_lower = result.stderr.lower()
            m = re.search(r'\[download\]\s+(.+?)\s+has already been downloaded', result.stderr, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        logger.error(f"CLI download failed (rc={result.returncode}): {result.stderr[:300]}")
        return None
    except subprocess.TimeoutExpired:
        logger.error("CLI download timed out")
        return None
    except Exception as ex:
        logger.error(f"CLI download error: {ex}")
        return None


def _pick_format_and_download(url, opts, quality, is_audio):
    os.makedirs(MEDIA_DIR, exist_ok=True)

    if not _HAS_FFMPEG:
        probe = _probe_formats(url, opts)
        if probe is None:
            logger.warning("Python API probe failed, trying CLI fallback...")
            ext = 'mp3' if is_audio else 'mp4'
            cli_path = os.path.join(MEDIA_DIR, f'%(id)s.{ext}')
            cli_result = _cli_download(url, cli_path)
            if cli_result:
                return cli_result
            raise ValueError(
                "YouTube видео недоступно для скачивания. Возможные причины: "
                "видео удалено, youtube заблокировал запрос (устаревший yt-dlp?), "
                "требуется авторизация или видео недоступно в регионе сервера."
            )

    if _HAS_FFMPEG and is_audio:
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    elif _HAS_FFMPEG and not is_audio:
        opts['format'] = 'bestvideo+bestaudio/best'
        if quality:
            opts['format'] = f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]'
        opts['merge_output_format'] = 'mp4'
    else:
        formats_to_try = ['bestaudio', 'best'] if is_audio else ['best', 'bestvideo']
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
        raise ValueError(f"YouTube: {last_ex}")

    logger.info(f'yt-dlp opts: format={opts.get("format")}, ffmpeg={_HAS_FFMPEG}')
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return _resolve_yt_path(ydl, info)


async def _download_yt_video(url, quality=None):
    def _dl():
        try:
            return _pick_format_and_download(url, dict(_YT_DL_OPTS), quality, is_audio=False)
        except yt_dlp.utils.DownloadError as ex:
            raise ValueError(f"YouTube: {ex}")
        except ValueError:
            raise
        except Exception as ex:
            logger.error(f"yt-dlp unexpected error: {ex}", exc_info=True)
            raise ValueError(f"YouTube: {ex}")

    return await asyncio.to_thread(_dl)


async def _download_yt_audio(url):
    def _dl():
        try:
            return _pick_format_and_download(url, dict(_YT_DL_OPTS), quality=None, is_audio=True)
        except yt_dlp.utils.DownloadError as ex:
            raise ValueError(f"YouTube: {ex}")
        except ValueError:
            raise
        except Exception as ex:
            logger.error(f"yt-dlp unexpected error: {ex}", exc_info=True)
            raise ValueError(f"YouTube: {ex}")

    return await asyncio.to_thread(_dl)


async def _download_instagram_video(url):
    def _dl():
        try:
            opts = dict(_YT_DL_OPTS)
            opts['format'] = 'best'
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return _resolve_yt_path(ydl, info)
        except yt_dlp.utils.DownloadError as ex:
            raise ValueError(f"Instagram: {ex}")
    return await asyncio.to_thread(_dl)


async def _download_tiktok_video(url):
    def _dl():
        try:
            opts = dict(_YT_DL_OPTS)
            opts['format'] = 'best'
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return _resolve_yt_path(ydl, info)
        except yt_dlp.utils.DownloadError as ex:
            raise ValueError(f"TikTok: {ex}")
    return await asyncio.to_thread(_dl)


async def run_download(event_edit_func, url, mode='video', quality=None, timeout=600):
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


async def send_and_clean(event_edit_func, client, chat_id, filepath, caption=''):
    if not filepath or not os.path.exists(filepath):
        return
    try:
        size_mb = os.path.getsize(filepath) / 1024 / 1024
    except OSError:
        return
    if size_mb > _max_file_size:
        await event_edit_func(f"❌ Слишком большой файл (> {_max_file_size} МБ).")
    else:
        await event_edit_func("📤 **Отправляю файл...**")
        try:
            await client.send_file(chat_id, filepath, caption=caption)
        except Exception as ex:
            await event_edit_func(f"❌ Ошибка отправки: {ex}")
    await asyncio.sleep(5)
    for _ in range(3):
        try:
            os.remove(filepath)
            break
        except OSError:
            await asyncio.sleep(1)
