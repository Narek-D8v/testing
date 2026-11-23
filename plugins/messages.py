# -*- coding: utf-8 -*-
import asyncio
import logging
import random
import re
import string
import time
from html import escape as html_escape

from telethon import events
from telethon.extensions import markdown as tl_markdown
from telethon.utils import get_display_name

from client import client
from core import db, owner_filter, respond, command_cooldown
from helpers import resolve_peer, normalize_command_text, entity_label
import ui


from telethon.tl.functions.messages import SendMessageRequest, SendReactionRequest
from telethon.tl.types import (
    MessageEntityBlockquote, MessageEntityItalic, MessageEntityPre,
    MessageEntitySpoiler, MessageEntityTextUrl, ReactionEmoji,
)


@client.on(events.NewMessage(pattern=r'!type(?:\s+(fast|slow|matrix|glitch))?\s+(.+)', func=owner_filter))
async def type_cmd(e):

    mode = e.pattern_match.group(1) or 'normal'
    text = e.pattern_match.group(2).strip()
    if mode == 'fast':
        msg = await respond(e, "▌")
        for i in range(0, len(text), 2):
            chunk = text[:i + 2]
            await msg.edit(chunk + ("▌" if i + 2 < len(text) else ""))
            await asyncio.sleep(0.04)
        await msg.edit(text)
    elif mode == 'slow':
        msg = await respond(e, "▌")
        shown = ""
        for ch in text:
            shown += ch
            await msg.edit(shown + "▌")
            pause = 0.3 if ch in '.!?…' else 0.12 if ch in ',;:' else 0.07
            await asyncio.sleep(pause)
        await msg.edit(text)
    elif mode == 'matrix':
        CHARS = string.ascii_letters + string.digits + "@#%&"
        msg = await respond(e, "▓" * len(text))
        for step in range(len(text)):
            parts = list(text[:step])
            for _ in range(len(text) - step):
                parts.append(random.choice(CHARS))
            await msg.edit(''.join(parts))
            await asyncio.sleep(0.07)
        await msg.edit(text)
    elif mode == 'glitch':
        GLITCH = "░▒▓█▄▀■□▪▫"
        msg = await respond(e, "".join(random.choice(GLITCH) for _ in text))
        for _ in range(6):
            glitched = "".join(
                c if random.random() > 0.4 else random.choice(GLITCH)
                for c in text
            )
            await msg.edit(glitched)
            await asyncio.sleep(0.12)
        await msg.edit(text)
    else:
        msg = await respond(e, "▌")
        shown = ""
        for i, ch in enumerate(text):
            shown += ch
            if i % 2 == 0 or i == len(text) - 1:
                await msg.edit(shown + ("▌" if i < len(text) - 1 else ""))
                await asyncio.sleep(0.05)
        await msg.edit(text)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!echo (.+)', func=owner_filter))
async def echo_cmd(e):

    await e.delete()
    await client.send_message(e.chat_id, e.pattern_match.group(1).strip())
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!bold (.+)', func=owner_filter))
async def bold_cmd(e):

    await e.delete()
    await client.send_message(e.chat_id, f"**{e.pattern_match.group(1).strip()}**")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!italic (.+)', func=owner_filter))
async def italic_cmd(e):

    await e.delete()
    await client.send_message(e.chat_id, f"__{e.pattern_match.group(1).strip()}__")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!mono (.+)', func=owner_filter))
async def mono_cmd(e):

    await e.delete()
    await client.send_message(e.chat_id, f"`{e.pattern_match.group(1).strip()}`")
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!clean(?:\s+(\d+))?$', func=owner_filter))
async def clean_cmd(e):

    limit = int(e.pattern_match.group(1) or 10)
    my_id = (await client.get_me()).id
    await e.delete()
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        if msg.out or (msg.from_id and getattr(msg.from_id, 'user_id', None) == my_id):
            await msg.delete()
            count += 1
            await asyncio.sleep(0.1)
    info = await client.send_message(e.chat_id, ui.ok(f"Удалено **{count}** своих сообщений"))
    await asyncio.sleep(3)
    await info.delete()
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!purge(?:\s+(\d+))?$', func=owner_filter))
async def purge_cmd(e):

    limit = int(e.pattern_match.group(1) or 10)
    await e.delete()
    count = 0
    async for msg in client.iter_messages(e.chat_id, limit=limit):
        await msg.delete()
        count += 1
        await asyncio.sleep(0.04)
    info = await client.send_message(e.chat_id, ui.warn(f"Удалено **{count}** сообщений", title="Очистка"))
    await asyncio.sleep(3)
    await info.delete()
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!spam (\d+) (.+)', func=owner_filter))
async def spam_cmd(e):

    count, text = int(e.pattern_match.group(1)), e.pattern_match.group(2).strip()
    MAX_SPAM = 50
    if count < 1 or count > MAX_SPAM:
        await respond(e, ui.err(f"Допустимо от 1 до {MAX_SPAM} сообщений.", title="Спам"))
        return
    cooldown_key = f'spam_{e.chat_id}'
    remaining = command_cooldown.get(cooldown_key, 0) - time.time()
    if remaining > 0:
        await respond(e, ui.err(f"Подождите {int(remaining)} сек перед повторным спамом.",
                                title="Кулдаун"))
        return
    command_cooldown[cooldown_key] = time.time() + 30
    await e.delete()
    for _ in range(count):
        await client.send_message(e.chat_id, text)
        await asyncio.sleep(0.35)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!forward(?:\s+(\S+))?$', func=owner_filter))
async def forward_cmd(e):

    if not e.reply_to_msg_id:
        await respond(e, ui.usage("!forward @user|ID", title="Пересылка"))
        return
    text, _ = normalize_command_text(e)
    g = re.match(r'!forward\s+(\S+)', text, re.IGNORECASE)
    target = g.group(1) if g else None
    if not target:
        await respond(e, ui.usage("!forward @user|ID", title="Пересылка"))
        return
    try:
        ent = await resolve_peer(target)
        msg = await e.get_reply_message()
        await client.forward_messages(ent.id, msg)
        await respond(e, ui.ok(ui.kv("Чат", f"`{ent.id}`"), title="Переслано"))
    except Exception as ex:
        await respond(e, ui.err(f"{ex}", title="Пересылка"))
    db.bump_stat('cmds')



@client.on(events.NewMessage(pattern=r'!pin$', func=owner_filter))
async def pin_cmd(e):

    if not e.reply_to_msg_id:
        await respond(e, ui.usage("!pin", title="Закреп"))
        return
    target = await e.get_reply_message()
    if not target:
        await respond(e, ui.err("Сообщение уже удалено — закреплять нечего.", title="Закреп"))
        return
    await target.pin(notify=False)
    await e.delete()
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!unpin$', func=owner_filter))
async def unpin_cmd(e):

    target = await e.get_reply_message() if e.reply_to_msg_id else None
    if target:
        await target.unpin()
    else:
        # сообщение-ответ уже удалено — открепляем закреплённое в чате
        await client.unpin_message(e.chat_id)
    await e.delete()
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!copyall\s+(\d+)\s+(\S+)', func=owner_filter))
async def copyall_cmd(e):

    text, _ = normalize_command_text(e)
    g = re.match(r'!copyall\s+(\d+)\s+(\S+)', text, re.IGNORECASE)
    count, target_raw = (int(g.group(1)), g.group(2)) if g else (None, None)
    if not count:
        await respond(e, ui.usage("!copyall <n> @user|ID", title="Копирование"))
        db.bump_stat('cmds')
        return
    try:
        target = (await resolve_peer(target_raw)).id
    except Exception as ex:
        await respond(e, ui.err(f"Не найден: `{target_raw}` ({ex})", title="Копирование"))
        db.bump_stat('cmds')
        return
    await respond(e, ui.card("⏳ Копирование", ui.rows(("Сообщений", f"`{count}`"))))
    msgs = []
    async for m in client.iter_messages(e.chat_id, limit=count):
        msgs.append(m)
    msgs.reverse()
    copied = 0
    for m in msgs:
        try:
            await client.forward_messages(target, m)
            copied += 1
            await asyncio.sleep(0.4)
        except Exception:
            pass
    await respond(e, ui.ok(ui.rows(("Скопировано", f"**{copied}/{count}**"),
                                   ("Чат", f"`{target}`")), title="Копирование завершено"))
    db.bump_stat('cmds')



# Подпись под сообщениями !write: цитата с курсивом и ссылкой на бота.
WRITE_BOT_URL = 'https://t.me/nemusechkabot'
WRITE_FOOTER_PLAIN = 'Отправлено с помощью Немусечки UwU'


# Синтаксис клиента Telegram, которого нет в markdown-парсере Telethon.
# Код-спаны и блоки не трогаем: внутри них * и _ — обычные символы.
_CODE_SPAN_RE = re.compile(r'(```[\s\S]*?```|`[^`\n]*`)')
# *курсив*: Telethon понимает только __...__
_ITALIC_STAR_RE = re.compile(r'(?<!\\)\*(?![\s*])([^*\n]*?)(?<![\s\\])\*(?!\*)')
# _курсив_ (не задевая snake_case и уже готовое __...__)
_ITALIC_UNDERSCORE_RE = re.compile(
    r'(?<![\w\\])_(?![\s_])([^_\n]*?)(?<![\s\\_])_(?![\w])')
# ||спойлер||: в markdown Telethon спойлера нет — ставим маркер и позже
# заменяем его на MessageEntitySpoiler.
_SPOILER_RE = re.compile(r'(?<!\\)\|\|(?![\s|])([^|\n]*?)(?<![\s\\])\|\|')
_SPOILER_TOKEN = '\ue000{}\ue001'
# ```lang\n...\n```: Telethon не разбирает язык и оставляет его словом в
# тексте, поэтому такие блоки вынимаем сами и делаем MessageEntityPre.
_FENCE_RE = re.compile(r'```[ \t]*([^\s`]*)[ \t]*\r?\n([\s\S]*?)```')
_PRE_TOKEN = '\ue000p{}\ue001'


def _telegram_aliases(text):
    """Приводит синтаксис клиента Telegram к понятному Telethon.

    Возвращает (текст для markdown-парсера, тексты спойлеров).
    """
    parts = _CODE_SPAN_RE.split(text)
    spoilers = []

    def _take_spoiler(m):
        spoilers.append(m.group(1))
        return _SPOILER_TOKEN.format(len(spoilers) - 1)

    for i in range(0, len(parts), 2):      # нечётные элементы — код, не трогаем
        chunk = _ITALIC_STAR_RE.sub(r'__\1__', parts[i])
        chunk = _ITALIC_UNDERSCORE_RE.sub(r'__\1__', chunk)
        parts[i] = _SPOILER_RE.sub(_take_spoiler, chunk)
    return ''.join(parts), spoilers


def _u16_len(text):
    """Длина в UTF-16: в таких смещениях Telegram ждёт entities."""
    return len(text.encode('utf-16-le')) // 2


def _extract_fences(text):
    """Вынимает ```lang-блоки в (язык, код) и ставит маркеры на их место."""
    blocks = []

    def _take_fence(m):
        code = m.group(2)
        if code.endswith('\n'):            # перенос перед ``` не часть кода
            code = code[:-1]
            if code.endswith('\r'):
                code = code[:-1]
        blocks.append((m.group(1), code))
        return _PRE_TOKEN.format(len(blocks) - 1)

    return _FENCE_RE.sub(_take_fence, text), blocks


def _md_entities(text):
    """Markdown → (текст, entities).

    Понимает и синтаксис клиента Telegram: *курсив*, _курсив_, ||спойлер||,
    а также ```lang-блоки (с языком в MessageEntityPre).
    """
    text, fences = _extract_fences(text)
    prepared, spoilers = _telegram_aliases(text)
    plain, entities = tl_markdown.parse(prepared)
    return _expand_tokens(plain, entities, fences, spoilers)


def _expand_tokens(plain, entities, fences, spoilers):
    """Заменяет маркеры код-блоков и спойлеров настоящими сущностями.

    Идём справа налево, чтобы не сдвигать ещё не обработанные маркеры;
    смещения уже разобранных entities правим на разницу длин."""
    items = []
    for i, (lang, code) in enumerate(fences):
        token = _PRE_TOKEN.format(i)
        pos = plain.find(token)
        if pos != -1:
            items.append((pos, token, 'pre', (lang, code)))
    for i, inner in enumerate(spoilers):
        token = _SPOILER_TOKEN.format(i)
        pos = plain.find(token)
        if pos != -1:
            items.append((pos, token, 'spoiler', inner))
    for pos, token, kind, payload in sorted(items, key=lambda it: it[0],
                                            reverse=True):
        start = _u16_len(plain[:pos])
        token_len = _u16_len(token)
        end = start + token_len
        if kind == 'pre':
            lang, new_text = payload
            new_entities = []
            if new_text:
                new_entities.append(MessageEntityPre(
                    offset=start, length=_u16_len(new_text), language=lang))
        else:
            new_text, new_entities = _md_entities(payload)
            for ent in new_entities:
                ent.offset += start
            new_entities.append(MessageEntitySpoiler(
                offset=start, length=_u16_len(new_text)))
        delta = _u16_len(new_text) - token_len
        for ent in entities:
            if ent.offset >= end:
                ent.offset += delta
            elif ent.offset + ent.length > start:
                ent.length += delta
        entities.extend(new_entities)
        plain = plain[:pos] + new_text + plain[pos + len(token):]
    return plain, entities


def _write_payload(body):
    """Текст сообщения !write и его entities: тело + цитата-подпись."""
    text, entities = _md_entities(body)
    body_len = _u16_len(text)
    footer = 'Отправлено с помощью Немусечки UwU'
    footer_start = body_len + 1          # после добавленного \n
    entities.append(MessageEntityBlockquote(
        offset=footer_start, length=_u16_len(footer)))
    entities.append(MessageEntityItalic(
        offset=footer_start, length=_u16_len(footer)))
    entities.append(MessageEntityTextUrl(
        offset=footer_start + _u16_len('Отправлено с помощью '),
        length=_u16_len('Немусечки'),
        url=WRITE_BOT_URL,
    ))
    entities.sort(key=lambda ent: (ent.offset, -ent.length))
    return f'{text}\n{footer}', entities


async def _send_write(peer, text, entities):
    """Отправка с готовыми entities: без markdown-разбора и предпросмотра
    ссылки (иначе Telegram рисует под сообщением карточку бота)."""
    await client(SendMessageRequest(
        peer=await client.get_input_entity(peer),
        message=text,
        entities=list(entities),
        no_webpage=True,
    ))


def _user_link_html(peer):
    """Имя пользователя ссылкой на профиль плюс @username, если он есть.

    Для юзернейма ссылка t.me/<username> (открывает профиль), иначе
    tg://user?id=<id> (работает и без юзернейма).
    """
    name = get_display_name(peer) or entity_label(peer)
    uname = getattr(peer, 'username', None)
    uid = getattr(peer, 'id', None)
    if uname:
        url = f'https://t.me/{uname}'
    elif uid is not None:
        url = f'tg://user?id={uid}'
    else:
        url = None
    name_html = html_escape(name)
    if url:
        name_html = f'<a href="{html_escape(url)}">{name_html}</a>'
    if uname:
        name_html += f' (@{html_escape(uname)})'
    return name_html


@client.on(events.NewMessage(pattern=r'!write(?:\s|$)', func=owner_filter))
async def write_cmd(e):
    """Написать пользователю от лица аккаунта. Только ЛС: группы/каналы
    не поддерживаются (ID должен быть положительным). Текст — markdown,
    внизу сообщения — цитата с подписью бота."""
    text, _ = normalize_command_text(e)
    m = re.match(r'!write\s+(\S+)\s+([\s\S]+)$', text, re.IGNORECASE)
    if not m or not m.group(2).strip():
        await respond(e, ui.usage("!write @user|ID <текст>", title="Написать"))
        db.bump_stat('cmds')
        return
    target_raw, body = m.group(1), m.group(2).strip()
    try:
        peer = await resolve_peer(target_raw, positive_only=True)
    except Exception as ex:
        await respond(e, ui.err(f"Не найден: `{target_raw}` ({ex})", title="Написать"))
        db.bump_stat('cmds')
        return
    send_error = None
    try:
        # разметку отдаём entities: HTML-парсер Telethon теряет спойлеры
        payload, entities = _write_payload(body)
        await _send_write(peer, payload, entities)
    except Exception as ex:
        # разметка не ушла — отправляем текст как есть, чтобы не потерять,
        # но ошибку не глотаем: покажем её в подтверждении
        send_error = ex
        logging.warning('!write: разметка не отправилась (%s), шлю текстом', ex)
        try:
            await client.send_message(
                peer, f"{body}\n{WRITE_FOOTER_PLAIN}", link_preview=False)
        except Exception as ex2:
            await respond(e, ui.err(f"Не удалось отправить: {ex2}", title="Написать"))
            db.bump_stat('cmds')
            return
    try:
        await e.delete()
    except Exception:
        pass
    # подтверждение только если команда набрана не в чате цели
    if e.chat_id and e.chat_id != getattr(peer, 'id', None):
        try:
            note = "✅ Написано"
            if send_error is not None:
                note = f"⚠️ Ушло без разметки: {send_error}"
            info = await client.send_message(
                e.chat_id,
                f"{note}\n\nКому: {_user_link_html(peer)}",
                parse_mode='html', link_preview=False
            )
            await asyncio.sleep(8 if send_error else 5)
            await info.delete()
        except Exception:
            pass
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!react (.+)', func=owner_filter))
async def react_cmd(e):

    if not e.reply_to_msg_id:
        await respond(e, ui.usage("!react 👍", title="Реакция"))
        return
    emoji = e.pattern_match.group(1).strip()
    try:
        await client(SendReactionRequest(
            peer=e.chat_id,
            msg_id=e.reply_to_msg_id,
            reaction=[ReactionEmoji(emoticon=emoji)]
        ))
        await e.delete()
    except Exception as ex:
        await respond(e, ui.err(f"Не удалось поставить реакцию: {ex}", title="Реакция"))
    db.bump_stat('cmds')
