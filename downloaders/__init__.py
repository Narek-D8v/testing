# downloaders — загрузчики медиа, разбитые по слоям.
#   common   — окружение (cookies/ffmpeg/yt-dlp), прокси, прогресс, форматы;
#   platforms — адресаты площадок: YouTube, Instagram, TikTok, Pinterest, generic;
#   pipeline — очередь по платформам, run_download, отправка и очистка файлов.
# Публичный API сохранён: остальной код импортирует его из пакета.

from .common import (
    _COOKIES_PATH,
    _COOKIES_VALID,
    _yt_id,
    set_max_file_size,
)
from .pipeline import _platform_key, run_download, send_and_clean
from .platforms import _is_direct_media

__all__ = [
    'run_download',
    'send_and_clean',
    'set_max_file_size',
    '_COOKIES_PATH',
    '_COOKIES_VALID',
    '_yt_id',
    '_platform_key',
    '_is_direct_media',
]
