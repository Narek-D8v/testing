import os
import logging

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Корень проекта: им пользуются блокировка экземпляра и пути к cookies,
# чтобы не зависеть от текущей рабочей директории процесса.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_PATH = os.path.join(BASE_DIR, 'bot.lock')

API_ID = os.environ.get('API_ID')
API_HASH = os.environ.get('API_HASH')
PORT = int(os.environ.get('PORT', 8080))
STRING_SESSION = os.environ.get('STRING_SESSION')
OWNER_ID_RAW = os.environ.get('OWNER_ID')
MEDIA_DIR = os.environ.get('MEDIA_DIR', './media')

if not API_ID or not API_HASH:
    raise ValueError("API_ID и API_HASH обязательны. Укажите их в .env")
if not OWNER_ID_RAW:
    raise ValueError("OWNER_ID обязателен. Укажите Telegram ID владельца в .env")
OWNER_ID = int(OWNER_ID_RAW)

PROXY = os.environ.get('PROXY')
PROXY_LIST = [p.strip() for p in os.environ.get('PROXY_LIST', '').split(',') if p.strip()]

MAX_COOLDOWN_ENTRIES = 500
# Лимит отправки: для user-аккаунтов Telegram — до 4 ГБ
MAX_FILE_SIZE_MB = int(os.environ.get('MAX_FILE_SIZE_MB', '4096'))

# Язык ответов бота: ru (по умолчанию) | en — см. helpers.t()
BOT_LANG = (os.environ.get('BOT_LANG') or 'ru').strip().lower()

# Минимальный шаг опроса ONWATCH для «горячих» целей (сек). Опрос идёт
# батчем (один GetUsers-запрос на все созревшие цели), поэтому 1с — безопасно:
# короткие сессии («вышла на 2 секунды») не должны теряться между опросами.
# Ниже 1с опускать нет смысла (FloodWait и лишний трафик).
def _clamp_interval(raw, default, low, high):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


WATCH_MIN_INTERVAL = _clamp_interval(
    os.environ.get('ONWATCH_MIN_INTERVAL'), 1.0, 1.0, 30.0)

# Строгий режим ONWATCH: уведомление при КАЖДОМ входе в сеть.
# Сессия разрывается по любому наблюдённому exit, поэтому обычный кулдаун
# нужен только против дребезга статуса внутри одной сессии. После реального
# выхода действует минимальный зазор (чтобы event и поллер не дали дубль).
WATCH_FIRE_COOLDOWN = _clamp_interval(
    os.environ.get('ONWATCH_FIRE_COOLDOWN'), 3.0, 0.0, 120.0)
WATCH_REENTRY_MIN_GAP = _clamp_interval(
    os.environ.get('ONWATCH_REENTRY_MIN_GAP'), 1.0, 0.0, 30.0)

# ===== EXTERNAL NOTIFY BOT =====
NOTIFY_BOT_TOKEN = os.environ.get('NOTIFY_BOT_TOKEN')
NOTIFY_CHAT_ID = os.environ.get('NOTIFY_CHAT_ID')


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on', 'y')


NOTIFY_ONLINE_WATCH = _bool(os.environ.get('NOTIFY_ONLINE_WATCH'), True)
NOTIFY_SESSIONS = _bool(os.environ.get('NOTIFY_SESSIONS'), True)
NOTIFY_PROTECT = _bool(os.environ.get('NOTIFY_PROTECT'), True)
NOTIFY_PM_DELETE = _bool(os.environ.get('NOTIFY_PM_DELETE'), True)
NOTIFY_PM_EDIT = _bool(os.environ.get('NOTIFY_PM_EDIT'), True)
# сохранение медиа по реплаю/удалению — отдельный флаг (раньше висел
# на NOTIFY_PM_DELETE и ломался вместе с ним)
NOTIFY_PM_MEDIA = _bool(os.environ.get('NOTIFY_PM_MEDIA'), True)
NOTIFY_ERRORS = _bool(os.environ.get('NOTIFY_ERRORS'), True)

# Токен веб-панели /stats: задан — доступ по ?token=... или X-Token;
# пуст — только с localhost.
WEB_TOKEN = os.environ.get('WEB_TOKEN', '').strip()

# ===== AI =====
# Основной провайдер — Gemini (REST, без устаревшего SDK), резервный —
# OpenRouter. Ключи необязательны: без них AI-команды отвечают понятной
# ошибкой, а остальной бот работает как раньше.
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '').strip()
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash').strip()
GEMINI_FALLBACK_MODELS = tuple(
    m.strip() for m in os.environ.get('GEMINI_FALLBACK_MODELS', 'gemini-2.0-flash').split(',')
    if m.strip()
)
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '').strip()
OPENROUTER_MODEL = os.environ.get('OPENROUTER_MODEL', 'deepseek/deepseek-chat:free').strip()
try:
    AI_TEMPERATURE = float(os.environ.get('AI_TEMPERATURE', '0.9'))
except (TypeError, ValueError):
    AI_TEMPERATURE = 0.9
AI_TTS_VOICE = os.environ.get('AI_TTS_VOICE', 'ru-RU-SvetlanaNeural').strip()
AI_MAX_HISTORY = int(os.environ.get('AI_MAX_HISTORY', '200') or 200)
# Сколько секунд не отвечать одному собеседнику повторно (анти-флуд)
AI_AUTO_COOLDOWN = int(os.environ.get('AI_AUTO_COOLDOWN', '15') or 15)
# Настроен ли хотя бы один провайдер: без ключей не копим историю ЛС.
AI_ENABLED = bool(GEMINI_API_KEY or OPENROUTER_API_KEY)
# Анализ эмодзи-реакций в ЛС (короткий опрос каждые ~25 с)
AI_REACTIONS = _bool(os.environ.get('AI_REACTIONS'), True)
# Ретеншн AI-истории читает storage.py из AI_HISTORY_PER_PEER / AI_HISTORY_TOTAL
# напрямую, чтобы тесты хранилища не зависели от обязательных API_ID/OWNER_ID.
