import sys, json, logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

import yt_dlp

ver = yt_dlp.version.__version__
logging.info(f"yt-dlp version: {ver}")

url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
url2 = "https://youtu.be/xVgY929D5P8"

# 1) reference video
try:
    opts = dict(quiet=True, no_warnings=True, extract_flat=True, skip_download=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        logging.info(f"1) ref OK: title={info.get('title', '?')[:60]}")
except Exception as e:
    logging.error(f"1) ref FAIL: {e}")

# 2) extract_flat on problem video
try:
    opts = dict(quiet=True, no_warnings=True, extract_flat=True, skip_download=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url2, download=False)
        logging.info(f"2) extract_flat OK: title={info.get('title', '?')[:60]}")
except Exception as e:
    logging.error(f"2) extract_flat FAIL: {e}")

# 3) full extract on problem video
try:
    opts = dict(quiet=True, no_warnings=True, extract_flat=False, skip_download=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url2, download=False)
        logging.info(f"3) full OK: title={info.get('title', '?')[:60]}, formats={len(info.get('formats') or [])}")
except Exception as e:
    logging.error(f"3) full FAIL: {e}")

import subprocess

def run_yt(args):
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'yt_dlp'] + args,
            capture_output=True, text=True, timeout=30
        )
        return result
    except Exception as e:
        return type('R', (), dict(returncode=-1, stdout='', stderr=str(e)))()

# 4) CLI flat
r = run_yt(['--flat-playlist', '-J', url2])
if r.stdout:
    logging.info(f"4) CLI flat OK: title={json.loads(r.stdout).get('title', '?')[:60]}")
else:
    logging.error(f"4) CLI flat FAIL (rc={r.returncode}): {r.stderr[:400]}")

# 5) CLI list-formats
r = run_yt(['--list-formats', url2])
if r.returncode == 0:
    logging.info(f"5) --list-formats:\n{r.stdout[:500]}")
else:
    logging.warning(f"5) --list-formats FAIL (rc={r.returncode}): {r.stderr[:400]}")

# 6) CLI with retries
r = run_yt(['--flat-playlist', '-J', '--retries', '10', '--sleep-requests', '3', url2])
if r.stdout:
    logging.info(f"6) CLI retry OK: title={json.loads(r.stdout).get('title', '?')[:60]}")
else:
    logging.error(f"6) CLI retry FAIL: {r.stderr[:400]}")

# 7) CLI with cookies file
import os
cpath = os.path.join(os.path.dirname(__file__), 'cookies.txt')
if os.path.exists(cpath):
    r = run_yt(['--flat-playlist', '-J', '--cookies', cpath, url2])
    if r.stdout:
        logging.info(f"7) CLI cookies OK: title={json.loads(r.stdout).get('title', '?')[:60]}")
    else:
        logging.error(f"7) CLI cookies FAIL: {r.stderr[:400]}")
else:
    logging.info("7) cookies.txt not found, skipping")
