from telethon import TelegramClient
from telethon.sessions import StringSession

from config import API_ID, API_HASH, STRING_SESSION, validate_config

# sequential_updates=False (значение по умолчанию — фиксируем явно): обновления
# разбираются параллельно, поэтому медленный send_message в одном обработчике
# не задерживает реакцию караула на следующий typing/статус.
# catch_up=False — осознанный выбор для ONWATCH: пачка апдейтов, накопленных
# за оффлайн, пришла бы с опозданием и переписала бы статус задним числом,
# а пропущенную сессию караул определяет по was_online (flicker).
# timeout — таймаут установки соединения (не чтения): после разрыва линка
# переподключение начинается быстрее, а ONWATCH реагирует на вход цели уже
# после реконнекта, поэтому лишние секунды здесь = потерянная точность.
# retry_delay/connection_retries оставлены дефолтными (1с / 5 попыток).
_KW = dict(
    sequential_updates=False,
    catch_up=False,
    timeout=5,
    auto_reconnect=True,
)


class LazyClient:
    """TelegramClient, создаваемый при первом реальном обращении.

    Плагины регистрируют хендлеры через `@client.on(...)` на этапе импорта —
    это не требует API_ID/API_HASH и не создаёт файл сессии. Клиент и все
    отложенные хендлеры создаются в момент запуска (`client.start()` и т.п.),
    когда `config.validate_config()` уже проверил обязательные секреты.
    """

    def __init__(self):
        self._client = None
        self._pending_handlers = []

    def _ensure(self):
        if self._client is None:
            validate_config()
            if STRING_SESSION:
                self._client = TelegramClient(
                    StringSession(STRING_SESSION), int(API_ID), API_HASH, **_KW)
            else:
                self._client = TelegramClient(
                    'my_userbot', int(API_ID), API_HASH, **_KW)
            for args, kwargs, handler in self._pending_handlers:
                self._client.on(*args, **kwargs)(handler)
            self._pending_handlers.clear()
        return self._client

    def on(self, *args, **kwargs):
        def decorator(handler):
            if self._client is None:
                self._pending_handlers.append((args, kwargs, handler))
            else:
                self._client.on(*args, **kwargs)(handler)
            return handler
        return decorator

    def __getattr__(self, name):
        return getattr(self._ensure(), name)


client = LazyClient()
