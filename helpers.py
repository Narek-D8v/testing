# helpers.py — общие функции, используемые и main.py, и плагинами.
# Единая точка: сущности Telegram, карточка статуса, защита от FloodWait,
# отложенное удаление сообщений, подавление уведомлений о своих удалениях.

import asyncio
import re

from telethon.errors import FloodWaitError
from telethon.tl.types import MessageEntityMention, MessageEntityMentionName

from client import client
from config import BOT_LANG, logger
from core import db, state
from notifier import notify
import ui



def t(ru, en):
    """Локализация: t('Загрузка', 'Download'). Строки — по ключу BOT_LANG в .env.
    По умолчанию ru; при BOT_LANG=en возвращается английский вариант."""
    return ru if BOT_LANG != 'en' else en


# ── Удаления, которые сделал сам юзербот (ghost/autodel) — о них уведомлять не надо ──
_pm_suppress_delete = set()


def suppress_pm_delete(msg_id):
    try:
        _pm_suppress_delete.add(int(msg_id))
    except Exception:
        return
    if len(_pm_suppress_delete) > 5000:
        _pm_suppress_delete.clear()


# ── Разрешение цели команды: '@user' / 'user' / числовой ID ──
# (у многих юзернейма нет — тогда target указывается числом)

async def resolve_entity(target):
    t = (target or '').strip()
    if not t:
        raise ValueError('не указан пользователь')
    if re.fullmatch(r'-?\d+', t):
        try:
            return await client.get_entity(int(t))
        except Exception:
            # id может не найтись в кэше сессии — пробуем строковым ключом
            return await client.get_entity(t)
    uname = t.lstrip('@').lower()
    try:
        # сначала штатно (имя, юзернейм, телефон, id) — работает если юзер уже в диалогах
        return await client.get_entity(t.lstrip('@'))
    except Exception:
        pass
    try:
        # @lucky_m_01 vs lucky_M_01: Telegram сравнивает username в диалогах
        # регистрозависимо, поэтому ищем вручную по нижнему регистру
        async for d in client.iter_dialogs():
            u = getattr(d.entity, 'username', None)
            if u and u.lower() == uname:
                return d.entity
    except Exception:
        pass
    raise ValueError(f'пользователь {t} не найден (нет в диалогах или ошибка сети)')


def entity_label(ent, fallback=None):
    """@username, а если его нет — id."""
    u = getattr(ent, 'username', None)
    if u:
        return f'@{u}'
    if fallback:
        return fallback
    return f'id{getattr(ent, "id", "")}'


class IdOnlyPeer:
    """Заглушка для числового ID, которого нет в кэше сессии.

    Командам нужен только ent.id (и иногда username), поэтому резолв по ID
    не должен ломаться из-за отсутствующего access_hash. Помечаем объект
    id_only, чтобы команды не выдавали выдуманное имя за реальное.
    """

    def __init__(self, uid):
        self.id = int(uid)
        self.first_name = f'ID {uid}'
        self.last_name = ''
        self.username = None
        self.bot = False
        self.id_only = True


async def resolve_peer(target, id_fallback=True, positive_only=False):
    """Единый резолв цели команды: '@user' / числовой Telegram ID.

    В отличие от resolve_entity(), числовой ID при неудачном поиске в кэше
    превращается в IdOnlyPeer — команда всё равно получает настоящий ID.
    positive_only запрещает отрицательные ID (это группы/каналы, не users).
    """
    t = (target or '').strip()
    if re.fullmatch(r'-?\d+', t):
        uid = int(t)
        if positive_only and uid <= 0:
            raise ValueError('ID пользователя должен быть положительным')
        try:
            return await resolve_entity(t)
        except Exception:
            if id_fallback:
                return IdOnlyPeer(uid)
            raise
    return await resolve_entity(t)


def _utf16_index(text, u16_offset):
    """Telegram отдаёт смещения entities в UTF-16, Python строки — в кодпоинтах."""
    total = 0
    for i, ch in enumerate(text):
        if total >= u16_offset:
            return i
        total += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def normalize_command_text(event):
    """Текст команды, где @упоминания заменены каноническим '@username' или ID.

    Telegram-упоминание по имени без username (MessageEntityMentionName) в
    raw_text выглядит как обычное имя, поэтому регулярки команд его не видят.
    Заменяем span на числовой ID. Возвращает (текст, [токены упоминаний]).
    """
    msg = getattr(event, 'message', event)
    text = getattr(msg, 'message', None) or getattr(event, 'raw_text', '') or ''
    spans = []
    for ent in (getattr(msg, 'entities', None) or []):
        if isinstance(ent, MessageEntityMentionName):
            canonical = str(ent.user_id)
        elif isinstance(ent, MessageEntityMention):
            start = _utf16_index(text, ent.offset)
            end = _utf16_index(text, ent.offset + ent.length)
            raw = text[start:end].strip()
            if not raw:
                continue
            canonical = raw if raw.startswith('@') else '@' + raw.lstrip('@')
        else:
            continue
        start = _utf16_index(text, ent.offset)
        end = _utf16_index(text, ent.offset + ent.length)
        spans.append((start, end, canonical))
    out = text
    for start, end, canonical in sorted(spans, reverse=True):
        out = out[:start] + canonical + out[end:]
    return out, [s[2] for s in sorted(spans)]


def _md_clean(text):
    """Убирает markdown-символы из имени: иначе карточка ломает разметку."""
    return re.sub(r'[*_`]', '', str(text or '')).strip()


async def _pm_monitor_label(limit=5):
    """Строка для карточки: за какими ЛС слежка выключена (!pmwatch)."""
    muted = sorted(db.get_pm_muted_chats())
    if not muted:
        return "все ЛС", True
    labels = []
    for cid in muted[:limit]:
        try:
            ent = await client.get_entity(cid)
            name = _md_clean(f"{getattr(ent, 'first_name', '') or ''} "
                             f"{getattr(ent, 'last_name', '') or ''}")
            uname = getattr(ent, 'username', None)
            if name and uname:
                labels.append(f"{name} @{uname}")
            elif uname:
                labels.append(f"@{uname}")
            else:
                labels.append(name or f"`{cid}`")
        except Exception:
            # аккаунт удалён / нет доступа — показываем хотя бы ID
            labels.append(f"`{cid}`")
    extra = len(muted) - len(labels)
    tail = f" …и ещё {extra}" if extra > 0 else ''
    return "все, кроме: " + ", ".join(labels) + tail, False


def _onwatch_interval_label():
    """Текущий шаг опроса ONWATCH: база и эффективные интервалы.

    База считается той же функцией, что у поллера (_poll_base), поэтому
    карточка не расходится с фактическим расписанием. Импорт локальный:
    plugins.watch импортирует helpers, модульный импорт был бы циклом."""
    try:
        from plugins.watch import _poll_base, _poll_gap
        tracks = len(db.get_online_watches())
    except Exception as ex:
        logger.debug(f"[STATUS] onwatch interval: {ex}")
        return "—", False
    if not tracks:
        return "нет треков", False
    base = _poll_base(tracks)
    active = _poll_gap(base, 'online')
    idle = _poll_gap(base, 'offline')
    return (f"`{active:g}с` онлайн · `{idle:g}с` оффлайн · треков `{tracks}`"), True


async def build_status_card():
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    s = db.all_stats()
    afk_status = f"вкл — {state.afk_reason or 'без причины'}" if state.afk_start_time else "выкл ❌"
    pm_label, pm_all = await _pm_monitor_label()
    onwatch_label, onwatch_on = _onwatch_interval_label()

    def _yn(v):
        return "вкл ✅" if v else "выкл ❌"

    return ui.card(
        t("📊 Статус UserBot", "📊 Bot Status"),
        ui.rows(
            ("Профиль", f"{me.first_name} {me.last_name or ''}".strip(), "👤"),
            ("Чатов", f"`{len(dialogs)}`", "💬"),
            ("Автоответчик", ("💤 " if state.auto_reply_enabled else "☀️ ") + _yn(state.auto_reply_enabled), "🤖"),
            ("Ghost", _yn(state.ghost_mode), "👻"),
            ("Cover", _yn(state.cover_enabled), "🛡️"),
            ("Silent", _yn(state.silent_enabled), "🤐"),
            ("Shadow", _yn(state.shadow_enabled), "👤"),
            ("Lock", _yn(state.lock_enabled), "🔒"),
            ("Mute", _yn(state.mute_enabled), "🔇"),
            ("Тайпинг", _yn(state.typing_enabled), "⌨️"),
            ("Автоудаление", _yn(state.autodel_enabled), "🗑️"),
            ("Задержка ответа", f"`{state.reply_delay}с`", "⏳"),
            ("Прочтение", _yn(state.readreceipt_enabled), "👁️"),
            ("AFK", afk_status, "😴"),
            ("Sudo", f"`{len(state.sudo_users)}`", "👑"),
            ("Мониторинг ЛС", ("👁 " if pm_all else "") + pm_label, "🎯"),
            ("ONWATCH", onwatch_label, "⚡"),
            ("Аптайм", f"`{state.uptime}`", "⏱"),
            ("Команд выполнено", f"`{s.get('cmds', 0)}`", "📨"),
        ),
        hint=t("`!help` — справка по командам", "`!help` — command reference"),
    )


async def safe_flood(coro_factory, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except FloodWaitError as e:
            logger.warning(f"FloodWait: {e.seconds}s, попытка {attempt + 1}/{max_retries}")
            try:
                await notify.error_alert("FloodWait", f"{e.seconds} секунд")
            except Exception:
                pass
            if attempt < max_retries - 1:
                await asyncio.sleep(e.seconds + 1)
            else:
                raise
        except Exception as ex:
            try:
                await notify.error_alert("Ошибка отправки", str(ex))
            except Exception:
                pass
            raise


async def shadow_delete_msg(msg, delay=None):
    d = delay or state.shadow_delay or 5
    await asyncio.sleep(d)
    try:
        suppress_pm_delete(getattr(msg, 'id', 0))
        await msg.delete()
    except Exception as ex:
        # сообщение уже удалено / нет прав — это норма, но молча терять
        # причину нельзя: раньше ошибка тут не была видна вообще
        logger.debug(f"[SHADOW-DEL] msg={getattr(msg, 'id', '?')}: {ex}")
