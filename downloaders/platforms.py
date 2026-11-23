import asyncio
import glob
import os
import re
import time
import urllib.request

import aiohttp
import yt_dlp

from config import MEDIA_DIR, logger


from .common import (has_ffmpeg, _dl_rotate, _pick_format_and_download, _proxy_candidates, _resolve_yt_path, _with_proxy, _yt_opts)

async def _download_yt_video(url, quality=None):
    def _dl(proxy=None):
        try:
            return _pick_format_and_download(url, _with_proxy(_yt_opts(), proxy), quality, is_audio=False)
        except yt_dlp.utils.DownloadError as ex:
            raise ValueError(f"YouTube: {ex}")
        except ValueError:
            raise
        except Exception as ex:
            logger.error(f"yt-dlp unexpected error: {ex}", exc_info=True)
            raise ValueError(f"YouTube: {ex}")

    return await asyncio.to_thread(_dl_rotate, _dl)


async def _download_yt_audio(url, bitrate=None):
    def _dl(proxy=None):
        try:
            return _pick_format_and_download(url, _with_proxy(_yt_opts(), proxy), quality=bitrate, is_audio=True)
        except yt_dlp.utils.DownloadError as ex:
            raise ValueError(f"YouTube: {ex}")
        except ValueError:
            raise
        except Exception as ex:
            logger.error(f"yt-dlp unexpected error: {ex}", exc_info=True)
            raise ValueError(f"YouTube: {ex}")

    return await asyncio.to_thread(_dl_rotate, _dl)


async def _download_instagram_video(url):
    def _dl(proxy=None):
        try:
            opts = _with_proxy(_yt_opts(), proxy)
            opts['format'] = 'b'
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return _resolve_yt_path(ydl, info)
        except yt_dlp.utils.DownloadError as ex:
            raise ValueError(f"Instagram: {ex}")
        except ValueError:
            raise
        except Exception as ex:
            logger.error(f"instagram unexpected error: {ex}", exc_info=True)
            raise ValueError(f"Instagram: {ex}")

    return await asyncio.to_thread(_dl_rotate, _dl)


async def _download_tiktok_video(url):
    def _dl(proxy=None):
        opts = _with_proxy(_yt_opts(), proxy)
        opts['format'] = 'b'
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                fp = _resolve_yt_path(ydl, info)
                if fp:
                    ext = os.path.splitext(fp)[1].lower()
                    if ext not in ('.m4a', '.mp3', '.aac', '.ogg'):
                        return fp
                    logger.info(f"TikTok: видео нет, только аудио ({fp}) — пробую картинки")
                    return None
        except yt_dlp.utils.DownloadError as ex:
            raise ValueError(f"TikTok: {ex}")
        except Exception as ex:
            logger.warning(f"TikTok video error: {ex}")
        return None

    try:
        fp = await asyncio.to_thread(_dl_rotate, _dl)
    except ValueError:
        fp = None
    if fp:
        return fp

    images = await _scrape_tiktok_images(url)
    if images:
        return images
    raise ValueError("TikTok: видео или карточки не доступны (IP может быть заблокирован)")


async def _scrape_tiktok_images(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36'}

    working = None
    html = None
    for proxy in [p for p in _proxy_candidates() if p] or [None]:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
                kwargs = {'headers': headers, 'allow_redirects': True}
                if proxy:
                    kwargs['proxy'] = proxy
                async with s.get(url, **kwargs) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text(errors='ignore')
                    working = proxy
                    break
        except Exception as ex:
            logger.warning(f"TikTok page fetch через {proxy or 'напрямую'} не удалась: {ex}")
            continue
    if html is None:
        return None

    candidates = []
    m = re.search(r'<meta[^>]+(?:property|name)="og:image"[^>]+content="([^"]+)"', html, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content="([^"]+)"[^>]+(?:property|name)="og:image"', html, re.I)
    if m:
        candidates.append(m.group(1).replace('&amp;', '&'))
    for s in re.findall(r'data-e2e="slide_img"[^>]*src="([^"]+)"', html):
        candidates.append(s.replace('&amp;', '&'))
    for s in re.findall(r'<img[^>]+src="([^"]+)"', html):
        candidates.append(s.replace('&amp;', '&'))

    seen, urls = set(), []
    for u in candidates:
        u = u.rstrip(')')
        if not u.startswith('http') or u in seen:
            continue
        seen.add(u)
        low = u.lower()
        if 'tiktokcdn' in low or 'image' in low or low.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
            urls.append(u)

    if not urls:
        return None

    os.makedirs(MEDIA_DIR, exist_ok=True)
    paths = []
    for i, u in enumerate(urls[:12], 1):
        ext = os.path.splitext(u.split('?')[0])[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
            ext = '.jpg'
        path = os.path.join(MEDIA_DIR, f'tiktok_{int(time.time() * 1000)}_{i}{ext}')
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s2:
                kwargs = {'headers': {**headers, 'Referer': 'https://www.tiktok.com/'}}
                if working:
                    kwargs['proxy'] = working
                async with s2.get(u, **kwargs) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.read()
            if data:
                with open(path, 'wb') as f:
                    f.write(data)
                if os.path.getsize(path) > 0:
                    paths.append(path)
        except Exception as ex:
            logger.warning(f"TikTok image {i} failed: {ex}")
            continue
    return paths or None


_GEN_OPTS = {
    'outtmpl': os.path.join(MEDIA_DIR, '%(id)s.%(ext)s'),
    'quiet': True,
    'no_warnings': True,
    'noplaylist': True,
    'playlist_items': '1',
    'extractor_retries': 3,
    'fragment_retries': 3,
    'retry_sleep': lambda n: 5 + n * 2,
    'sleep_interval_requests': 1,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    },
}


def _is_direct_media(url):
    return bool(re.search(r'\.(mp4|webm|mkv|avi|mov|flv|wmv|mp3|aac|ogg|wav|m4a)($|\?)', url.lower()))


def _find_m3u8_in_html(html):
    urls = re.findall(r'(https?://[^"\'\s<>]+?\.m3u8[^"\'\s<>]*)', html)
    if not urls:
        urls = re.findall(r'["\']([^"\']+\.m3u8[^"\']*)["\']', html)
    return urls


def _resolve_url(base, uri):
    if uri.startswith('http://') or uri.startswith('https://'):
        return uri
    from urllib.parse import urljoin
    return urljoin(base, uri)


async def _download_m3u8_video(m3u8_url, output_path):
    import m3u8
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(m3u8_url) as resp:
                if resp.status != 200:
                    return None
                content = await resp.text()
        playlist = m3u8.loads(content)
        seg_urls = []
        if playlist.is_variant:
            top = max(playlist.playlists, key=lambda p: p.stream_info.resolution[1] if p.stream_info.resolution else 0)
            variant_url = _resolve_url(m3u8_url, top.uri)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                async with s.get(variant_url) as resp:
                    if resp.status != 200:
                        return None
                    content = await resp.text()
            playlist = m3u8.loads(content)
        for seg in playlist.segments:
            uri = _resolve_url(m3u8_url, seg.uri)
            seg_urls.append(uri)
        if not seg_urls:
            return None
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as s:
            with open(output_path, 'wb') as f:
                for seg_url in seg_urls:
                    try:
                        async with s.get(seg_url) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                f.write(data)
                    except Exception as ex:
                        # битый сегмент не должен ронять всю загрузку, но и
                        # молчать о нём нельзя — файл получится дырявым
                        logger.debug(f"m3u8 segment {seg_url[:80]} пропущен: {ex}")
                        continue
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
    except Exception as ex:
        logger.warning(f"m3u8 download failed: {ex}")
    return None


async def _download_generic(url, mode, event_edit_func):
    await event_edit_func("🌐 Пробую yt-dlp...")
    try:
        opts = dict(_GEN_OPTS)
        if mode == 'audio':
            if has_ffmpeg():
                opts['format'] = 'ba/b'
                opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
            else:
                # без ffmpeg оставляем аудио в исходном контейнере, но m4a
                # предпочтительнее: webm Telegram показывает как видео
                opts['format'] = 'bestaudio[ext=m4a]/bestaudio'
        filename = await asyncio.to_thread(
            lambda: _dl_generic_sync(url, opts, is_audio=(mode == 'audio')))
        if filename:
            return filename
    except Exception as ex:
        logger.warning(f"yt-dlp generic failed: {ex}")

    # В audio-режиме прямую ссылку качаем только если это аудиоформат:
    # видео-контейнер без конвертации — это уже не аудио
    _direct_is_audio = bool(re.search(r'\.(mp3|aac|m4a|ogg|wav|flac)($|\?)', url.lower()))
    if _is_direct_media(url) and (mode != 'audio' or _direct_is_audio):
        await event_edit_func("📥 Скачиваю напрямую...")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as s:
                async with s.get(url) as resp:
                    if resp.status == 200:
                        ext = os.path.splitext(url.split('?')[0])[1] or '.mp4'
                        path = os.path.join(MEDIA_DIR, f'direct_{int(time.time())}{ext}')
                        with open(path, 'wb') as f:
                            while True:
                                chunk = await resp.content.read(65536)
                                if not chunk:
                                    break
                                f.write(chunk)
                        if os.path.getsize(path) > 0:
                            return path
        except Exception as ex:
            logger.warning(f"Direct download failed: {ex}")

    if mode == 'audio':
        # Фолбэки ниже (картинка, m3u8) возвращают не-аудио — вместо них честная ошибка
        raise ValueError(
            "Не удалось получить аудио по этой ссылке. "
            + ("Проверь ссылку или используй YouTube-адрес."
               if has_ffmpeg() else
               "Без ffmpeg аудио из видео не извлекается: установи ffmpeg "
               "(winget install ffmpeg / apt install ffmpeg).")
        )

    await event_edit_func("🖼 Пробую скачать как картинку...")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(url, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    og_img = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html, re.IGNORECASE)
                    if og_img:
                        img_url = og_img.group(1).replace('&amp;', '&')
                        ext = os.path.splitext(img_url.split('?')[0])[1] or '.jpg'
                        path = os.path.join(MEDIA_DIR, f'img_{int(time.time())}{ext}')
                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s2:
                            async with s2.get(img_url) as img_resp:
                                if img_resp.status == 200:
                                    with open(path, 'wb') as f:
                                        async for chunk in img_resp.content.iter_chunked(65536):
                                            f.write(chunk)
                                    if os.path.getsize(path) > 0:
                                        return path
    except Exception as ex:
        logger.warning(f"Image fallback failed: {ex}")

    await event_edit_func("🔍 Ищу m3u8...")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(url, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    m3u8_urls = _find_m3u8_in_html(html)
                    for m3u8_url in m3u8_urls:
                        path = os.path.join(MEDIA_DIR, f'm3u8_{int(time.time())}.mp4')
                        result = await _download_m3u8_video(m3u8_url, path)
                        if result:
                            return result
    except Exception as ex:
        logger.warning(f"m3u8 search failed: {ex}")

    return None


def _dl_generic_sync(url, opts, is_audio=False):
    last_ex = None
    # Формат из opts ('ba' для аудио) НЕ затираем дефолтным: раньше первая
    # итерация делала del opts['format'] и yt-dlp качал лучшее видео.
    # Для аудио видео-фолбэки ('b'/'best') запрещены.
    attempts = [None] if is_audio else [None, 'b', 'best']
    for fmt_override in attempts:
        try:
            if fmt_override:
                opts['format'] = fmt_override
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filepath = None
                if info.get('requested_downloads'):
                    filepath = info['requested_downloads'][0].get('filepath')
                if not filepath or not os.path.exists(filepath or ''):
                    filepath = ydl.prepare_filename(info)
                if filepath and os.path.exists(filepath):
                    return filepath
                video_id = info.get('id')
                if video_id:
                    matches = sorted(glob.glob(os.path.join(MEDIA_DIR, f'{video_id}.*')), key=os.path.getmtime, reverse=True)
                    if matches:
                        return matches[0]
        except yt_dlp.utils.DownloadError as ex:
            last_ex = ex
            if 'No video formats' in str(ex):
                continue
            raise
    last_ex_str = str(last_ex) if last_ex else ''

    if not is_audio and 'No video formats' in last_ex_str:
        try:
            with yt_dlp.YoutubeDL({**opts, 'skip_download': True, 'quiet': True, 'no_warnings': True, 'format': None}) as ydl:
                info = ydl.extract_info(url, download=False)
            thumb_url = info.get('thumbnail') or (info.get('entries') or [{}])[0].get('thumbnail')
            if thumb_url:
                ext = os.path.splitext(thumb_url.split('?')[0])[1] or '.jpg'
                path = os.path.join(MEDIA_DIR, f'img_{info.get("id", int(time.time()))}{ext}')
                urllib.request.urlretrieve(thumb_url, path)
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    return path
        except Exception as ex:
            logger.warning(f"Thumbnail download failed: {ex}")

    return None


async def _download_pinterest_pin(url):
    yt_path = await _try_ytdlp_pinterest(url)
    if yt_path:
        return yt_path

    logger.info("No video from yt-dlp, scraping image...")
    originals_url, fallback_url = await _scrape_pinterest_image(url)
    if originals_url:
        try:
            return await _generic_direct_download(originals_url, fallback_736x=fallback_url)
        except ValueError as ex:
            if fallback_url:
                logger.info(f"originals failed ({ex}), trying 736x fallback")
                return await _generic_direct_download(fallback_url)
            raise
    raise ValueError("Pinterest: не удалось найти медиа на странице")


async def _try_ytdlp_pinterest(url):
    def _dl():
        opts = _yt_opts()
        opts['format'] = 'bv*+ba/b'
        opts['merge_output_format'] = 'mp4'
        opts['extractor_args'] = {'pinterest': {'video': ['true']}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                fp = _resolve_yt_path(ydl, info)
                if fp:
                    return fp
        except yt_dlp.utils.DownloadError as ex:
            if 'No video formats' in str(ex):
                return None
            logger.warning(f"yt-dlp Pinterest error: {ex}")
            return None
        except Exception as ex:
            logger.warning(f"yt-dlp Pinterest error: {ex}")
        return None
    return await asyncio.to_thread(_dl)


async def _scrape_pinterest_image(url):
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        async with s.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}, allow_redirects=True) as resp:
            if resp.status != 200:
                return None, None
            html = await resp.text()

    og = re.search(r'<meta[^>]+(?:property|name)="og:image"[^>]+content="([^"]+)"', html, re.IGNORECASE)
    if not og:
        og = re.search(r'<meta[^>]+content="([^"]+)"[^>]+(?:property|name)="og:image"', html, re.IGNORECASE)
    if og:
        img = og.group(1).replace('&amp;', '&')
        fallback = None
        if '/736x/' in img:
            fallback = img
            img = img.replace('/736x/', '/originals/')
        return img, fallback

    img_urls = re.findall(r'(https://i\.pinimg\.com/(?:originals|736x|1200x|474x|236x|170x)/[a-zA-Z0-9/_-]+\.(?:jpg|jpeg|png|gif|webp))', html)
    if not img_urls:
        return None, None

    seen = set()
    unique = []
    for u in img_urls:
        n = u.rstrip(')')
        if n not in seen:
            seen.add(n)
            unique.append(n)

    for prefix in ('/originals/', '/1200x/', '/736x/', '/474x/', '/236x/', '/170x/'):
        for u in unique:
            if prefix in u:
                if prefix != '/originals/':
                    fallback = u
                    u = u.replace(prefix, '/originals/', 1)
                    return u, fallback
                return u, None
    return unique[0], None


async def _generic_direct_download(url, *, referer='https://www.pinterest.com/', fallback_736x=None):
    ext = os.path.splitext(url.split('?')[0].split('/')[-1])[1]
    if not ext or ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.webm', '.mov'):
        ext = '.jpg'
    path = os.path.join(MEDIA_DIR, f'pin_{int(time.time())}{ext}')
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
        'Referer': referer,
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
        async with s.get(url, headers=headers) as resp:
            if resp.status == 403 and fallback_736x:
                logger.info("originals blocked (403), fallback to 736x")
                return await _generic_direct_download(fallback_736x, referer=referer)
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status} при скачивании {url}")
            with open(path, 'wb') as f:
                while True:
                    chunk = await resp.content.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    raise ValueError("Файл пуст после скачивания")


