# -*- coding: utf-8 -*-
"""Одноразовый скрипт: приведение всех команд с пользователем к единому резолву."""
import io
import re


def read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def write(path, s):
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(s)


def sub(path, old, new, count=1):
    s = read(path)
    n = s.count(old)
    assert n >= count, f'{path}: не найден фрагмент ({n} < {count}): {old[:70]!r}'
    s = s.replace(old, new, count)
    write(path, s)
    print('OK', path, old.splitlines()[0][:60])


def sub_re(path, pattern, new, count=1):
    s = read(path)
    s2, n = re.subn(pattern, lambda m: new, s, count=count, flags=re.S)
    assert n >= count, f'{path}: regex не совпал ({n} < {count}): {pattern[:70]}'
    write(path, s2)
    print('OK(re)', path, pattern[:60])


# ───────────────────────── system.py ─────────────────────────
p = 'plugins/system.py'
s = read(p)
s = s.replace('# marker_shell\n', '')
if '\nimport re\n' not in s:
    s = s.replace('import os\n', 'import os\nimport re\n', 1)
write(p, s)

sub(p,
    r"""@client.on(events.NewMessage(pattern=r'!setreply(?:\s+(@?\w+))?(?:\s+(.+))?', func=owner_filter))
async def setreply_cmd(e):

    g = e.pattern_match
    target, text = g.group(1), g.group(2)
""",
    r"""@client.on(events.NewMessage(pattern=r'!setreply(?:\s+(\S+))?(?:\s+(.+))?', func=owner_filter))
async def setreply_cmd(e):

    text, _ = normalize_command_text(e)
    g = re.match(r'!setreply(?:\s+(@?[\w-]+))?(?:\s+(.+))?', text, re.IGNORECASE)
    target, text = (g.group(1), g.group(2)) if g else (None, None)
""")

sub(p,
    "    if target and not (target.startswith('@') or target.isdigit() or target.lower() == 'default'):",
    "    if target and not (target.startswith('@') or target.lstrip('-').isdigit()\n"
    "                       or target.lower().lstrip('@') == 'default'):")

sub(p,
    r"""@client.on(events.NewMessage(pattern=r'!sudo(?:\s+(on|off)\s+(\S+))?\s*$', func=owner_filter))
async def sudo_cmd(e):

    g = e.pattern_match
    action = g.group(1)
    target = g.group(2)
""",
    r"""@client.on(events.NewMessage(pattern=r'!sudo(?:\s+(on|off)\s+(\S+))?\s*$', func=owner_filter))
async def sudo_cmd(e):

    text, _ = normalize_command_text(e)
    g = re.match(r'!sudo(?:\s+(on|off)\s+(\S+))?\s*$', text, re.IGNORECASE)
    action, target = (g.group(1), g.group(2)) if g else (None, None)
""")

sub(p,
    "        db.bump_stat('cmds')\n        return\n    try:\n        ent = await resolve_entity(target)",
    "        db.bump_stat('cmds')\n        return\n"
    "    if not target:\n"
    "        await respond(e, ui.usage('!sudo on @username|ID',\n"
    "                                  '!sudo off @username|ID', title='Sudo'))\n"
    "        db.bump_stat('cmds')\n"
    "        return\n"
    "    try:\n        ent = await resolve_peer(target, positive_only=True)")

new_id = '''@client.on(events.NewMessage(pattern=r'!id(?:\\s+(\\S+))?$', func=owner_filter))
async def id_cmd(e):

    text, _ = normalize_command_text(e)
    g = re.match(r'!id(?:\\s+(\\S+))?$', text, re.IGNORECASE)
    target = g.group(1) if g else None
    chat = await e.get_chat()
    lines = [ui.kv("ID чата", f"`{chat.id}`", "🆔")]
    if target:
        try:
            ent = await resolve_peer(target)
            lines.append(ui.kv("ID пользователя", f"`{ent.id}`", "👤"))
            uname = getattr(ent, 'username', None)
            if uname:
                lines.append(ui.kv("Username", f"@{uname}", "🔖"))
        except Exception as ex:
            await respond(e, ui.err(f"Не найден: `{target}` ({ex})", title="ID"))
            db.bump_stat('cmds')
            return
    elif e.reply_to_msg_id:
        r = await e.get_reply_message()
        lines += [
            ui.kv("ID отправителя", f"`{r.sender_id}`", "👤"),
            ui.kv("ID сообщения", f"`{r.id}`", "📨"),
        ]
        if r.sender and getattr(r.sender, 'username', None):
            lines.append(ui.kv("Username", f"@{r.sender.username}", "🔖"))
    else:
        me = await client.get_me()
        lines.append(ui.kv("Мой ID", f"`{me.id}`", "👤"))
    hint = ("Ответьте на сообщение или укажите @username / ID" if not target
            else "!id @username|ID")
    await respond(e, ui.card("🆔 ID", "\\n".join(lines), hint=hint))
    db.bump_stat('cmds')


'''
sub_re(p, r"@client\.on\(events\.NewMessage\(pattern=r'!id\$'.*?(?=\n@client\.on)", new_id)

# ───────────────────────── security.py ─────────────────────────
p = 'plugins/security.py'
s = read(p)
if '\nimport re\n' not in s:
    s = s.replace('import os\n', 'import os\nimport re\n', 1)
s = s.replace('from helpers import resolve_entity',
              'from helpers import resolve_peer, normalize_command_text')
write(p, s)

sub(p,
    r"@client.on(events.NewMessage(pattern=r'!pmwatch(?:\s+(mute|unmute)\s+(\S+))?\s*$', func=owner_filter))",
    r"@client.on(events.NewMessage(pattern=r'!pmwatch(?:\s+(\S+)(?:\s+(\S+))?)?\s*$', func=owner_filter))")

sub(p,
    "    g = e.pattern_match\n    action, target = g.group(1), g.group(2)\n",
    "    text, _ = normalize_command_text(e)\n"
    "    g = re.match(r'!pmwatch(?:\\s+(\\S+)(?:\\s+(\\S+))?)?\\s*$',\n"
    "                 text, re.IGNORECASE)\n"
    "    action, target = (g.group(1), g.group(2)) if g else (None, None)\n")

sub(p,
    "    if action == 'unmute' and target.lower() == 'all':",
    "    if action not in ('mute', 'unmute'):\n"
    "        await respond(e, ui.usage('!pmwatch mute @user|ID',\n"
    "                                  '!pmwatch unmute @user|ID|all',\n"
    "                                  title='Мониторинг ЛС'))\n"
    "        db.bump_stat('cmds')\n"
    "        return\n"
    "    if not target:\n"
    "        await respond(e, ui.usage('!pmwatch mute @user|ID',\n"
    "                                  '!pmwatch unmute @user|ID|all',\n"
    "                                  title='Мониторинг ЛС'))\n"
    "        db.bump_stat('cmds')\n"
    "        return\n\n"
    "    if action == 'unmute' and target.lower() == 'all':")

sub(p,
    "        ent = await resolve_entity(target)\n        cid = ent.id",
    "        ent = await resolve_peer(target)\n        cid = ent.id")

# ───────────────────────── profile.py ─────────────────────────
p = 'plugins/profile.py'
s = read(p)
if '\nimport re\n' not in s:
    s = s.replace('import asyncio\n', 'import asyncio\n')  # нет asyncio — вставим ниже
    s = s.replace('\nfrom telethon import events', '\nimport re\n\nfrom telethon import events', 1)
s = s.replace('from helpers import resolve_entity, entity_label',
              'from helpers import (resolve_peer, normalize_command_text,\n'
              '                     entity_label)')
s = s.replace('from telethon import events',
              'from telethon import events\n'
              'from telethon.errors import (UsernameInvalidError,\n'
              '                             UsernameNotOccupiedError, FloodWaitError)\n'
              'from telethon.tl.functions.contacts import ResolveUsernameRequest')
write(p, s)

new_avatar = '''@client.on(events.NewMessage(pattern=r'!avatar(?:\\s+(\\S+))?$', func=owner_filter))
async def avatar_cmd(e):

    text, _ = normalize_command_text(e)
    g = re.match(r'!avatar(?:\\s+(\\S+))?$', text, re.IGNORECASE)
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


'''
sub_re(p, r"@client\.on\(events\.NewMessage\(pattern=r'!avatar\$'.*?(?=\n@client\.on)", new_avatar)

sub(p,
    "    target = e.pattern_match.group(1).strip()\n    try:\n        ent = await resolve_entity(target)",
    "    text, _ = normalize_command_text(e)\n"
    "    g = re.match(r'!whois\\s+(.+)', text, re.IGNORECASE)\n"
    "    target = g.group(1).strip() if g else ''\n"
    "    try:\n        ent = await resolve_peer(target)")

new_uc = '''@client.on(events.NewMessage(pattern=r'!username_check(?:\\s+(.+))?$', func=owner_filter))
async def username_check_cmd(e):

    text, _ = normalize_command_text(e)
    g = re.match(r'!username_check\\s+(.+)', text, re.IGNORECASE)
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


'''
sub_re(p, r"@client\.on\(events\.NewMessage\(pattern=r'!username_check .*?(?=\n@client\.on|\Z)", new_uc)

# ───────────────────────── watch.py ─────────────────────────
p = 'plugins/watch.py'
s = read(p)
s = s.replace('from helpers import resolve_entity',
              'from helpers import resolve_peer, normalize_command_text')
s = s.replace("        text = (e.raw_text or '').strip()",
              "        text, _ = normalize_command_text(e)\n        text = text.strip()")
s = s.replace(r"(@?[\w\d_]+)", r"(@?[\w\d_-]+)")
s = s.replace('ent = await resolve_entity(target_raw)',
              'ent = await resolve_peer(target_raw)')
s = s.replace(
    "            try:\n                ent = await resolve_peer(target_raw)\n"
    "            except Exception as ex:\n"
    "                await respond(e, ui.err(f\"Не удалось найти пользователя: {ex}\",",
    "            try:\n                ent = await resolve_peer(target_raw, positive_only=True)\n"
    "            except Exception as ex:\n"
    "                await respond(e, ui.err(f\"Не удалось найти пользователя: {ex}\",")
s = s.replace("if not isinstance(ent, User):",
              "if not isinstance(ent, User) and not getattr(ent, 'id_only', False):")
if 'from telethon.tl.types import User' not in s:
    s = s.replace('from telethon.errors import FloodWaitError',
                  'from telethon.errors import FloodWaitError\n'
                  'from telethon.tl.types import User')
write(p, s)
print('OK plugins/watch.py')

# ───────────────────────── messages.py ─────────────────────────
p = 'plugins/messages.py'
s = read(p)
s = s.replace('from core import db, owner_filter, respond, command_cooldown',
              'from core import db, owner_filter, respond, command_cooldown\n'
              'from helpers import resolve_peer, normalize_command_text')
write(p, s)

new_forward = '''@client.on(events.NewMessage(pattern=r'!forward(?:\\s+(\\S+))?$', func=owner_filter))
async def forward_cmd(e):

    if not e.reply_to_msg_id:
        await respond(e, ui.usage("!forward @user|ID", title="Пересылка"))
        return
    text, _ = normalize_command_text(e)
    g = re.match(r'!forward\\s+(\\S+)', text, re.IGNORECASE)
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


'''
sub_re(p, r"@client\.on\(events\.NewMessage\(pattern=r'!forward .*?(?=\n@client\.on)", new_forward)

new_copy = '''@client.on(events.NewMessage(pattern=r'!copyall\\s+(\\d+)\\s+(\\S+)', func=owner_filter))
async def copyall_cmd(e):

    text, _ = normalize_command_text(e)
    g = re.match(r'!copyall\\s+(\\d+)\\s+(\\S+)', text, re.IGNORECASE)
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


'''
sub_re(p, r"@client\.on\(events\.NewMessage\(pattern=r'!copyall .*?(?=\n@client\.on)", new_copy)

# messages.py теперь использует re
s = read(p)
if '\nimport re\n' not in s:
    s = s.replace('import random\n', 'import random\nimport re\n', 1)
write(p, s)

# ───────────────────────── справка ─────────────────────────
p = 'help_data.py'
s = read(p)
repl = {
    "'syntax': '!setreply [@user|default] [текст]'": "'syntax': '!setreply [@user|ID|default] [текст]'",
    "'syntax': '!pmwatch [mute|unmute] @user|ID|all'": "'syntax': '!pmwatch [mute|unmute] @user|ID',",
    "'syntax': '!whois @ник'": "'syntax': '!whois @username|ID'",
    "'syntax': '!username_check @ник'": "'syntax': '!username_check @username|ID'",
    "'syntax': '!sudo [on|off] @user'": "'syntax': '!sudo [on|off] @username|ID'",
    "'syntax': '!id'": "'syntax': '!id [@username|ID]'",
    "'syntax': '!avatar'": "'syntax': '!avatar [@username|ID]'",
    "'syntax': '!forward [chat_id]'": "'syntax': '!forward [@user|ID]'",
    "'syntax': '!copyall [n] [chat_id]'": "'syntax': '!copyall [n] [@user|ID]'",
}
for old, new in repl.items():
    if old in s:
        s = s.replace(old, new, 1)
    else:
        print('WARN help_data:', old)
write(p, s)
print('OK help_data.py')

p = 'README.md'
s = read(p)
for old, new in {
    '`!setreply [@user\\|default] [текст]`': '`!setreply [@user\\|ID\\|default] [текст]`',
    '`!whois @ник`': '`!whois @username\\|ID`',
    '`!username_check @ник`': '`!username_check @username\\|ID`',
    '`!sudo [on\\|off] @user`': '`!sudo [on\\|off] @username\\|ID`',
    '`!id`': '`!id [@username\\|ID]`',
    '`!avatar`': '`!avatar [@username\\|ID]`',
    '`!forward [chat_id]`': '`!forward [@user\\|ID]`',
    '`!copyall [n] [chat_id]`': '`!copyall [n] [@user\\|ID]`',
}.items():
    if old in s:
        s = s.replace(old, new, 1)
    else:
        print('WARN README:', old)
write(p, s)
print('OK README.md')

print('DONE')
