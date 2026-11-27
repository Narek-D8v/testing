# -*- coding: utf-8 -*-

import re

from telethon import events
from telethon.errors import (UsernameInvalidError,
                             UsernameNotOccupiedError, FloodWaitError)
from telethon.tl.functions.contacts import ResolveUsernameRequest

from client import client
from core import db, owner_filter, respond
from helpers import resolve_peer, normalize_command_text
import ui



@client.on(events.NewMessage(pattern=r'!me$', func=owner_filter))
async def me_cmd(e):

    me = await client.get_me()
    photos = await client.get_profile_photos(me.id, limit=1)
    await respond(e, ui.card(
        "👤 Мой профиль",
        ui.rows(
            ("Имя", f"{me.first_name} {me.last_name or ''}".strip(), "📛"),
            ("ID", f"`{me.id}`", "🆔"),
            ("Username", f"@{me.username}" if me.username else "нет", "🔰"),
            ("Телефон", f"`{me.phone}`" if me.phone else "скрыт", "📱"),
            ("Аватар", "есть ✅" if photos else "нет ❌", "🖼"),
            ("Verified", "да ✅" if me.verified else "нет ❌", "✔️"),
            ("Бот", "да ✅" if me.bot else "нет ❌", "🤖"),
        ),
        hint="`!avatar`, `!name`, `!lastname`, `!bio` — изменить профиль",
    ))
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!avatar(?:\s+(\S+))?$', func=owner_filter))
async def avatar_cmd(e):

    text, _ = normalize_command_text(e)
    g = re.match(r'!avatar(?:\s+(\S+))?$', text, re.IGNORECASE)
    target = g.group(1) if g else None
    if target:
        try:
            uid = (await resolve_peer(target, positive_only=True)).id
        except Exception as ex:
            await respond(e, ui.err(f"Не найден: `{target}` ({ex})", title="Аватарка"))
            db.bump_stat('cmds')
            return
    elif e.reply_to_msg_id:
        uid = (await e.get_reply_message()).sender_id
    else:
        uid = (await client.get_me()).id
    photos = await client.get_profile_photos(uid, limit=1)
    if photos:
        await e.reply(file=photos[0])
        await e.delete()
    else:
        await respond(e, ui.err("Аватарка не найдена",
                                hint="Ответьте на чужое сообщение или укажите @username / ID"))
    db.bump_stat('cmds')



@client.on(events.NewMessage(pattern=r'!name (.+)$', func=owner_filter))
async def name_cmd(e):

    n = e.pattern_match.group(1).strip()
    await client.edit_profile(first_name=n)
    await respond(e, ui.ok(ui.kv("Имя", f"**{n}**"), title="Имя изменено"))
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!lastname(?:\s+(.+))?$', func=owner_filter))
async def lastname_cmd(e):

    n = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(last_name=n)
    await respond(e, ui.ok(ui.kv("Фамилия", f"**{n}**"), title="Фамилия изменена")
                   if n else ui.ok("Фамилия удалена", title="Профиль"))
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!bio(?:\s+(.+))?$', func=owner_filter))
async def bio_cmd(e):

    t = (e.pattern_match.group(1) or '').strip()
    await client.edit_profile(about=t)
    await respond(e, ui.ok(ui.kv("Био", f"_{t}_"), title="Био обновлено")
                   if t else ui.ok("Био очищено", title="Профиль"))
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!whois (.+)$', func=owner_filter))
async def whois_cmd(e):

    text, _ = normalize_command_text(e)
    g = re.match(r'!whois\s+(.+)', text, re.IGNORECASE)
    target = g.group(1).strip() if g else ''
    try:
        ent = await resolve_peer(target)
        name = f"{getattr(ent, 'first_name', '') or ''} {getattr(ent, 'last_name', '') or ''}".strip() \
               or getattr(ent, 'title', '?')
        uname = f"@{ent.username}" if getattr(ent, 'username', None) else "нет"
        bot_ = "✅" if getattr(ent, 'bot', False) else "❌"
        ver = "✅" if getattr(ent, 'verified', False) else "❌"
        await respond(e, ui.card(
            "🔍 Пользователь",
            ui.rows(
                ("Имя", f"**{name}**", "📛"),
                ("ID", f"`{ent.id}`", "🆔"),
                ("Username", uname, "🔰"),
                ("Бот", bot_, "🤖"),
                ("Verified", ver, "✔️"),
            ),
            hint="`!username_check @ник|ID` — проверить username",
        ))
    except Exception as ex:
        await respond(e, ui.err(f"Не найден `{target}`: {ex}",
                                title="Пользователь",
                                hint="Формат: `!whois @username|ID`"))
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!username_check(?:\s+(.+))?$', func=owner_filter))
async def username_check_cmd(e):

    text, _ = normalize_command_text(e)
    g = re.match(r'!username_check\s+(.+)', text, re.IGNORECASE)
    raw = g.group(1).strip() if g else ''
    if not raw:
        await respond(e, ui.usage('!username_check @username|ID',
                                  title='Проверка username'))
        db.bump_stat('cmds')
        return

    if raw.lstrip('-').isdigit():
        # ID: показываем, есть ли у пользователя username. Кэш может быть
        # пуст — тогда честно говорим «нет данных», а не «свободен».
        try:
            ent = await resolve_peer(raw, positive_only=True)
        except Exception as ex:
            await respond(e, ui.err(f"ID `{raw}` не найден: {ex}", title="Пользователь"))
            db.bump_stat('cmds')
            return
        uname = getattr(ent, 'username', None)
        if getattr(ent, 'id_only', False):
            status = 'нет данных (нет в кэше сессии)'
        else:
            status = 'занят ⚠️' if uname else 'свободен ✅'
        await respond(e, ui.card(
            "🔍 Проверка username",
            ui.rows(
                ("ID", f"`{ent.id}`", "🆔"),
                ("Имя", getattr(ent, 'first_name', None) or '—', "👤"),
                ("Username", f"@{uname}" if uname else "отсутствует", "🔰"),
                ("Статус", status, "ℹ️"),
            ),
        ))
        db.bump_stat('cmds')
        return

    uname = raw.lstrip('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', uname):
        await respond(e, ui.err(f"Некорректный username: `{raw}`",
                                title="Проверка username",
                                hint="username: 5–32 символа, латиница, цифры и _"))
        db.bump_stat('cmds')
        return
    # Реальный global-lookup Telegram, а не «не нашли в диалогах ⇒ свободен».
    try:
        result = await client(ResolveUsernameRequest(uname))
    except (UsernameNotOccupiedError, UsernameInvalidError):
        await respond(e, ui.card("🔍 Проверка username",
                                 ui.rows(("Username", f"@{uname}", "🔰"),
                                         ("Статус", "свободен ✅", "ℹ️"))))
        db.bump_stat('cmds')
        return
    except FloodWaitError as ex:
        await respond(e, ui.err(f"Telegram просит подождать {ex.seconds}с — результат неизвестен",
                                title="Проверка username"))
        db.bump_stat('cmds')
        return
    except Exception as ex:
        await respond(e, ui.err(f"Проверка не удалась: {ex} — результат неизвестен",
                                title="Проверка username"))
        db.bump_stat('cmds')
        return
    user = result.users[0] if result.users else None
    name = getattr(user, 'first_name', None) or getattr(user, 'title', None) or '—'
    uid = getattr(user, 'id', None)
    await respond(e, ui.card(
        "🔍 Проверка username",
        ui.rows(
            ("Username", f"@{uname}", "🔰"),
            ("Статус", "занят ✅", "ℹ️"),
            ("Имя", name, "👤"),
            ("ID", f"`{uid}`" if uid else "—", "🆔"),
        ),
    ))
    db.bump_stat('cmds')


