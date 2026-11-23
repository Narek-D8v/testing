import asyncio
import os
import re
from collections import defaultdict

from config import logger
from utils import format_bytes
import ui


from .common import (_DownloadProgress, _current_progress, _ensure_runtime, _report_progress, get_max_file_size, _try_invidious)
from .platforms import (_download_generic, _download_instagram_video, _download_pinterest_pin, _download_tiktok_video, _download_yt_audio, _download_yt_video)

# ── Очередь загрузок: лок на платформу (разные платформы качаются параллельно,
#    две загрузки с одной платформы — последовательно) + позиция в очереди ──
_DL_LOCKS = {}
# Сколько загрузок в очереди на каждую платформу: общий счётчик давал двум
# параллельным загрузкам одинаковую «позицию 2»
_dl_pending = defaultdict(int)


def _platform_key(url):
    u = url.lower()
    if 'youtube.com' in u or 'youtu.be' in u:
        return 'youtube'
    if 'tiktok.com' in u:
        return 'tiktok'
    if 'instagram.com' in u:
        return 'instagram'
    if 'pinterest.com' in u or 'pin.it' in u:
        return 'pinterest'
    return 'generic'


async def run_download(event_edit_func, url, mode='video', quality=None, timeout=600):
    _ensure_runtime()  # ffmpeg/JS-рантайм/yt-dlp — только на реальной загрузке
    key = _platform_key(url)
    lock = _DL_LOCKS.setdefault(key, asyncio.Lock())
    _dl_pending[key] += 1
    pos = _dl_pending[key]
    prog = _DownloadProgress()
    reporter = None
    token = None
    try:
        if lock.locked():
            try:
                await event_edit_func(f"⏳ Очередь загрузок: **позиция {pos}**\n"
                                      "Дождусь своей очереди…")
            except Exception:
                pass
        async with lock:
            if pos > 1:
                try:
                    await event_edit_func("▶️ Очередь пройдена — скачиваю…")
                except Exception:
                    pass
            token = _current_progress.set(prog)
            reporter = asyncio.create_task(_report_progress(prog, event_edit_func))
            return await _run_download_impl(event_edit_func, url, mode, quality, timeout)
    finally:
        prog.stop()
        if reporter is not None:
            reporter.cancel()
        # Сбрасываем только СВОЁ значение: параллельная загрузка другой
        # платформы держит собственный контекст и не должна терять прогресс
        if token is not None:
            _current_progress.reset(token)
        _dl_pending[key] -= 1
        if _dl_pending[key] <= 0:
            _dl_pending.pop(key, None)


async def _run_download_impl(event_edit_func, url, mode='video', quality=None, timeout=600):
    logger.info(f"[download] Starting: {url} (mode={mode})")
    try:
        is_instagram = 'instagram.com' in url.lower()
        is_youtube = 'youtube.com' in url.lower() or 'youtu.be' in url.lower()
        is_tiktok = 'tiktok.com' in url.lower()
        is_pinterest = 'pinterest.com' in url.lower() or 'pin.it' in url.lower()
        if is_youtube:
            m = re.match(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[a-zA-Z0-9_-]{11})', url)
            if m:
                url = m.group(1)

        if mode == 'audio' and (is_tiktok or is_instagram or is_pinterest):
            raise ValueError(
                "!audio поддерживается только для YouTube и прямых ссылок; "
                "для TikTok / Instagram / Pinterest сначала скачай видео через !dl."
            )

        if is_pinterest:
            await event_edit_func("📥 Скачиваю из Pinterest...")
            filename = await asyncio.wait_for(
                _download_pinterest_pin(url), timeout=timeout
            )
        elif is_tiktok:
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
                    _download_yt_audio(url, quality), timeout=timeout
                )
            else:
                qual = f"{quality}p" if quality else None
                await event_edit_func(f"📥 Скачиваю видео ({qual or 'авто'})...")
                filename = await asyncio.wait_for(
                    _download_yt_video(url, quality), timeout=timeout
                )
        else:
            return await _download_generic(url, mode, event_edit_func)

        if isinstance(filename, list):
            filename = [p for p in filename if p and os.path.exists(p)]
            if filename:
                logger.info(f"[download] OK: {len(filename)} файл(ов) — {url}")
                return filename
            filename = None
        if filename and os.path.exists(filename):
            size = os.path.getsize(filename)
            if size < 512:
                logger.warning(f"[download] подозрительно маленький файл ({size} байт): {filename}")
                try:
                    os.remove(filename)
                except OSError:
                    pass
                filename = None
            else:
                logger.info(f"[download] OK: {filename} ({format_bytes(size)})")
                return filename
        logger.warning(f"[download] file not found or empty after download: {url}")
        return None
    except asyncio.TimeoutError:
        await event_edit_func(ui.err("Превышено время ожидания (10 мин).",
                                     title="Загрузка",
                                     hint="`!dl <URL>` — повторить попытку"))
        logger.warning(f"Download timeout: {url}")
        return None
    except ValueError as ex:
        logger.warning(f"Download error: {ex}")
        if is_youtube:
            await event_edit_func("🔄 Пробую через Invidious...")
            invidious_path = await _try_invidious(url, mode)
            if invidious_path:
                return invidious_path
        await event_edit_func(ui.err(f"{ex}", title="Загрузка"))
        return None
    except Exception as ex:
        logger.error(f"Download error: {ex}", exc_info=True)
        await event_edit_func(ui.err(f"{ex}", title="Загрузка"))
        return None


async def send_and_clean(event_edit_func, client, chat_id, filepath, caption='',
                         as_voice=False, as_document=False):
    if not filepath:
        return
    files = filepath if isinstance(filepath, list) else [filepath]
    files = [f for f in files if f and os.path.exists(f)]
    if not files:
        return
    send_kwargs = {}
    if as_voice:
        send_kwargs['voice_note'] = True   # отправка голосовым (аудио-сообщением)
    elif as_document:
        send_kwargs['force_document'] = True  # всегда файлом, без плеера
    size_limit = get_max_file_size()
    try:
        too_big = [f for f in files if os.path.getsize(f) / 1024 / 1024 > size_limit]
    except OSError:
        return
    if too_big:
        await event_edit_func(ui.err(f"Слишком большой файл (> {size_limit} МБ).",
                                     title="Загрузка"))
    else:
        await event_edit_func(ui.card("📤 Отправка", "отправляю файл…"))
        sent = []
        failed = []
        for f in files:
            try:
                logger.info(f"send file: {f} ({os.path.getsize(f)} байт)")
            except OSError:
                logger.warning(f"send file: {f} (размер недоступен)")
            try:
                await client.send_file(chat_id, f, caption=caption, **send_kwargs)
                sent.append(f)
            except Exception as ex:
                logger.warning(f"send as media failed ({f}): {ex}")
                try:
                    await client.send_file(chat_id, f, caption=caption, force_document=True)
                    sent.append(f)
                except Exception as ex2:
                    logger.error(f"send as document failed ({f}): {ex2}")
                    failed.append((f, ex2))
        if failed:
            names = ", ".join(os.path.basename(f) for f, _ in failed)
            await event_edit_func(ui.err(f"Не удалось отправить: {names}", title="Отправка"))
        elif sent:
            await event_edit_func(ui.ok(f"Отправлено файлов: **{len(sent)}**"))
    await asyncio.sleep(5)
    for f in files:
        for _ in range(3):
            try:
                os.remove(f)
                break
            except OSError:
                await asyncio.sleep(1)
