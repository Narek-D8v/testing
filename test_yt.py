import sys, json, logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

import yt_dlp

ver = yt_dlp.version.__version__
logging.info(f"yt-dlp version: {ver}")

# 1) extract_flat с известным видео
url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

try:
    opts = dict(quiet=True, no_warnings=True, extract_flat=True, skip_download=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        logging.info(f"1) extract_flat OK: title={info.get('title', '?')[:60]}")
except Exception as e:
    logging.error(f"1) extract_flat FAIL: {e}")

# 2) extract_flat с проблемным видео (от пользователя)
url2 = "https://youtu.be/xVgY929D5P8"

try:
    opts = dict(quiet=True, no_warnings=True, extract_flat=True, skip_download=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url2, download=False)
        logging.info(f"2) extract_flat OK: title={info.get('title', '?')[:60]}, formats={len(info.get('formats') or [])}")
except Exception as e:
    logging.error(f"2) extract_flat FAIL: {e}")

# 3) Без extract_flat (полная выгрузка)
try:
    opts = dict(quiet=True, no_warnings=True, extract_flat=False, skip_download=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url2, download=False)
        logging.info(f"3) full OK: title={info.get('title', '?')[:60]}, formats={len(info.get('formats') or [])}")
except Exception as e:
    logging.error(f"3) full FAIL: {e}")

# 4) subprocess прямой вызов (flat)
import subprocess
try:
    result = subprocess.run(
        [sys.executable, '-m', 'yt_dlp', '--flat-playlist', '-J', url2],
        capture_output=True, text=True, timeout=30
    )
    if result.stdout:
        data = json.loads(result.stdout)
        logging.info(f"4) CLI --flat-playlist OK: title={data.get('title', '?')[:60]}")
    else:
        logging.error(f"4) CLI FAIL (stderr): {result.stderr[:500]}")
except Exception as e:
    logging.error(f"4) CLI exception: {e}")

# 5) subprocess --list-formats
try:
    result = subprocess.run(
        [sys.executable, '-m', 'yt_dlp', '--list-formats', url2],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode == 0:
        logging.info(f"5) --list-formats output:\n{result.stdout[:500]}")
    else:
        logging.warning(f"5) --list-formats FAIL (stderr): {result.stderr[:500]}")
except Exception as e:
    logging.error(f"5) CLI --list-formats exception: {e}")

# 6) subprocess без кук
try:
    opts = ['--flat-playlist', '-J', '--no-cookies', url2]
    result = subprocess.run(
        [sys.executable, '-m', 'yt_dlp'] + opts,
        capture_output=True, text=True, timeout=30
    )
    if result.stdout:
        data = json.loads(result.stdout)
        logging.info(f"6) CLI no-cookies OK: title={data.get('title', '?')[:60]}")
    else:
        logging.error(f"6) CLI no-cookies FAIL: {result.stderr[:300]}")
except Exception as e:
    logging.error(f"6) CLI no-cookies exception: {e}")
