# -*- coding: utf-8 -*-
"""AI-блок: анализ переписки, редактура, медиа, автоответчик и реакции.

Команды работают только для владельца/ sudo (owner_filter). История ЛС
пишется в отдельную таблицу ai_msgs с ретеншном — она не зависит от
кэша анти-удаления и не растёт бесконечно.
"""

import asyncio
import os
import random
import shutil
import subprocess
import tempfile
import time

from telethon import events
from telethon.tl.types import (
    PeerUser,
    ReactionEmpty,
    UpdateBotMessageReaction,
    UpdateBotMessageReactions,
    UpdateMessageReactions,
    User,
)

from ai_engine import AIEngine, AIError
from ai_utils import (
    compose_card, html_to_plain, md_to_telegram_html, parse_ai_json,
    render_liar, render_mediation, render_promises, render_reality, render_roles,
    render_summary, render_vibe, safe_truncate_html, swap_layout, timestamp_label,
    transcript, transcript_dated, truncate,
)
from client import client
from config import (
    AI_AUTO_COOLDOWN,
    AI_ENABLED,
    AI_MAX_HISTORY,
    AI_REACTIONS,
    AI_TTS_VOICE,
    MEDIA_DIR,
    OWNER_ID,
    logger,
)
from core import db, owner_filter, respond, state
from helpers import resolve_peer
from notifier import notify
import ui

eng = AIEngine()
_me_cache = None

# Схемы для структурированных команд — отдельными константами, чтобы не
# экранировать фигурные скобки внутри f-строк с промптом.
_VIBE_SCHEMA = '''{
  "summary": "одна фраза об общем настроении",
  "scores": {"aggression": 0, "sympathy": 0, "engagement": 0, "trust": 0, "tension": 0},
  "evidence": [{"signal": "что заметил", "quote": "дословная цитата из переписки", "meaning": "почему это важно"}],
  "dynamics": [{"stage": "начало/середина/конец", "mood": "настроение на этапе", "why": "из-за чего"}],
  "triggers": ["что усиливает эмоции"],
  "red_flags": ["тревожный сигнал"],
  "advice": "одно конкретное предложение владельцу"
}'''

_LIAR_SCHEMA = '''{
  "verdict": "короткий вывод о согласованности слов",
  "credibility": 0,
  "summary": "одним предложением, в чём главное расхождение",
  "contradictions": [
    {"claim": "суть противоречия", "severity": "высокая|средняя|низкая",
     "quote_a": "первая дословная цитата", "context_a": "когда/при каких обстоятельствах",
     "quote_b": "вторая дословная цитата", "context_b": "когда/при каких обстоятельствах",
     "why": "почему это не сходится", "questions": ["что спросить именно по этому пункту"]}
  ],
  "unverified": ["утверждение без подтверждения"],
  "questions": ["вопрос, который прояснит ситуацию"],
  "advice": "как действовать владельцу"
}'''

_PROMISES_SCHEMA = '''{
  "new": [
    {"text": "дословное обещание", "who": "я|он(а)|имя участника",
     "due": "срок из переписки или пустая строка", "note": "контекст одним предложением"}
  ],
  "fulfilled": [123],
  "cancelled": [456],
  "summary": "одним предложением, что с обещаниями происходит"
}'''

_MEDIATION_SCHEMA = '''{
  "diagnosis": "нейтрально: в чём суть напряженности",
  "common_ground": ["что обоим важно"],
  "steps": [
    {"say": "готовая фраза от владельца", "why": "почему она сработает"}
  ],
  "boundaries": ["что не обещать и не терпеть"],
  "questions": ["вопрос для собеседника"],
  "advice": "короткий итог"
}'''

_REALITY_SCHEMA = '''{
  "facts": [{"quote": "дословная цитата", "when": "дата из переписки",
             "what": "что реально произошло"}],
  "interpretations": [{"claim": "как это трактуется",
                       "basis": "на чём основано",
                       "alternative": "другое объяснение"}],
  "verdict": "что подтверждено, а что — только интерпретация",
  "advice": "как не нагнетать дальше"
}'''

_ROLES_SCHEMA = '''{
  "participants": [
    {"user": "имя или ID участника", "summary": "одна фраза о роли в диалоге",
     "roles": [{"type": "манипулятор (по поведению)|уязвимая сторона|избегающий|инициатор конфликта|контролирующий|миротворец",
                "confidence": 75,
                "evidence": ["дословная цитата"], "why": "почему так выглядит"}]}
  ],
  "dynamic": "как роли сталкиваются и меняются",
  "caveats": "где данных не хватает"
}'''


async def _me():
    global _me_cache
    if _me_cache is None:
        _me_cache = await client.get_me()
    return _me_cache


def _target_name(ent):
    if not ent:
        return 'пользователь'
    name = getattr(ent, 'first_name', None) or getattr(ent, 'title', None)
    if not name:
        name = getattr(ent, 'username', None)
    return name or str(getattr(ent, 'id', 'пользователь'))


async def _resolve_target(event, mention=None):
    """@ник / ID → реплай → сам ЛС-чат."""
    if mention:
        return await resolve_peer(mention, positive_only=True)
    if event.reply_to_msg_id:
        try:
            replied = await event.get_reply_message()
            if replied and replied.sender_id:
                return await resolve_peer(str(replied.sender_id))
        except Exception:
            pass
    chat = await event.get_chat()
    if isinstance(chat, User):
        return chat
    return None


async def _last_text(event):
    """Текст для редактуры: реплай, аргумент команды или последняя реплика."""
    if event.reply_to_msg_id:
        replied = await event.get_reply_message()
        if replied and (replied.raw_text or '').strip():
            return (replied.sender_id, replied.raw_text)
    target = await _resolve_target(event)
    if target:
        hist = db.get_ai_msgs(target.id, limit=1)
        if hist and hist[0].get('text'):
            return (hist[0]['sender_id'], hist[0]['text'])
    return (None, None)


async def _grab_history(uid, n):
    hist = db.get_ai_msgs(uid, limit=n)
    if len(hist) >= n:
        return hist
    pulled = 0
    try:
        async for m in client.iter_messages(uid, limit=n):
            text = (m.raw_text or '').strip()
            if not text or text.startswith('!'):
                continue
            db.save_ai_msg(
                uid, m.sender_id or uid, text[:1000],
                ts=m.date.timestamp() if m.date else time.time(),
                msg_id=m.id,
            )
            pulled += 1
            if pulled % 50 == 0:
                await asyncio.sleep(0.1)
    except Exception as ex:
        logger.debug(f'AI grab_history: {ex}')
    return db.get_ai_msgs(uid, limit=n)


async def _gen(prompt, system=None, image=None, json_mode=False):
    """Запрос к AI. json_mode включает строгий JSON у провайдеров, где он есть."""
    try:
        return await eng.generate(
            prompt, system_prompt=system, image_bytes=image, json_mode=json_mode)
    except AIError:
        raise
    except Exception as ex:
        logger.warning(f'AI generate: {ex}', exc_info=True)
        raise AIError(f'AI ошибка: {ex}') from ex


async def _status(event, text):
    return await respond(event, text, parse_mode=None)


async def _finish(msg, text, parse_mode='html', chat_id=None):
    """Дописать/заменить статус готовым ответом.

    AI-ответы приходят в безопасном HTML: Telegram не умеет GitHub-разметку
    (**жирный**, > цитата, * список), а Markdown v1 Telethon падал на
    произвольных символах из модели. Если HTML всё же отвергнут — отправляем
    тот же текст без разметки.
    """
    text = safe_truncate_html(text, 3800) if parse_mode == 'html' else truncate(text, 3800)
    plain = html_to_plain(text) if parse_mode == 'html' else text
    attempts = ((parse_mode, text), (None, plain)) if parse_mode else ((None, plain),)
    for mode, body in attempts:
        try:
            if msg:
                return await msg.edit(body, parse_mode=mode)
            if chat_id:
                # Статус не отправился — ответ всё равно должен дойти до чата.
                return await client.send_message(chat_id, body, parse_mode=mode)
            return None
        except Exception as ex:
            logger.debug(f'[AI-FORMAT] отправка с parse_mode={mode} не удалась: {ex}')
            # Статусное сообщение могло быть удалено/запрещено к правке —
            # последний шанс донести ответ уже новым сообщением.
            if msg and chat_id:
                try:
                    return await client.send_message(chat_id, body, parse_mode=mode)
                except Exception as ex2:
                    logger.debug(f'[AI-FORMAT] send_message не удалась: {ex2}')
    return None


def _ai_result(title, answer, provider, renderer=None, meta=''):
    """Собрать HTML-карточку из ответа модели.

    renderer (render_vibe/render_liar/render_summary) получает разобранный
    JSON. Если модель не прислала корректный JSON — текст конвертируется
    из Markdown в HTML, а не показывается сырым.
    """
    data = parse_ai_json(answer)
    body = renderer(data) if renderer and data is not None else ''
    if not body.strip():
        body = md_to_telegram_html(answer)
    return compose_card(title, body, provider, meta)


def _ai_plain(answer):
    """Текст без разметки — для собственных сообщений от имени аккаунта."""
    return html_to_plain(md_to_telegram_html(answer))


def _split_text(text, limit=4000):
    text = (text or '').strip()
    if len(text) <= limit:
        return [text] if text else []
    return [text[i:i + limit] for i in range(0, len(text), limit)]


async def _send_owner(text):
    """Отчёт владельцу: внешний бот (если настроен), иначе «Избранное»."""
    try:
        if notify.enabled:
            if await notify.send(ui.to_html(text)):
                return True
    except Exception as ex:
        logger.warning(f'AI owner notify: {ex}')
    try:
        me = await _me()
        await client.send_message(me.id, text)
        return True
    except Exception as ex:
        logger.warning(f'AI saved messages: {ex}')
        return False


# ────────────────────────────────────────────────────────────
# История + AI-автоответчик в ЛС
# ────────────────────────────────────────────────────────────

_ai_cd = {}
_active_privates = {}


def _cooldown(key, sec=3):
    now = time.time()
    if _ai_cd.get(key, 0) > now:
        return int(_ai_cd[key] - now)
    _ai_cd[key] = now + sec
    if len(_ai_cd) > 500:
        cutoff = now - 3600
        for k in [k for k, v in _ai_cd.items() if v < cutoff]:
            _ai_cd.pop(k, None)
    return 0


def _system_for_auto(auto, uid):
    """Описание собеседника — это КТО пишет владельцу, а не роль ассистента.
    Манера речи всегда копируется у владельца (из этого или заданного чата)."""
    desc = (auto.get('prompt') or auto.get('role') or 'друг').strip()
    lines = [
        'Ты — автоответчик владельца юзербота. Отвечаешь ОТ ЕГО ИМЕНИ в личном чате, как сам владелец.',
        f'Собеседник, с которым ведёт переписку владелец: {desc}. '
        'Это описание того, КТО пишет владельцу (пол, характер, положение), а НЕ твоя роль. '
        'Обращайся к собеседнику корректно и не называй его «парнем»/«девушкой» без причины.',
    ]
    src_peer, src_n = uid, 50
    try:
        saved_src = (db.get_state('ai_style_src', '') or '').strip()
        saved_n = (db.get_state('ai_style_n', '') or '').strip()
        # Источником может быть и группа/канал — отрицательный ID.
        if saved_src:
            try:
                src_peer = int(saved_src)
            except (TypeError, ValueError):
                src_peer = uid
            if saved_n.isdigit():
                src_n = max(5, min(int(saved_n), 400))
    except Exception:
        pass
    me = _me_cache
    me_id = me.id if me else OWNER_ID
    style = db.get_ai_msgs(src_peer, limit=src_n, sender=me_id)
    if style:
        examples = [truncate(m['text'], 220) for m in style if m.get('text')][-22:]
        if src_peer == uid:
            lines.append('Так пишет владелец в этой переписке (копируй манеру, тон, длину сообщений, смайлы и орфографию):')
        else:
            lines.append(f'Так пишет владелец в чате #{src_peer} — копируй его манеру, тон, длину и смайлы:')
        lines.append('\n'.join('- ' + ex for ex in examples))
    facts = db.get_ai_facts(uid)
    if facts:
        lines.append('Факты о собеседнике, которые обязательно учитывать:\n- ' + '\n- '.join(facts))
    lines.append('Отвечай одним сообщением, естественно, как в переписке. Без пояснений.')
    return '\n'.join(lines)


def _user_prompt_response(uid, text):
    me = _me_cache
    me_id = me.id if me else OWNER_ID
    hist = db.get_ai_msgs(uid, limit=20)
    lines = ['Переписка с человеком (последние сообщения):']
    for m in hist:
        who = 'Я' if m['sender_id'] == me_id else 'Собеседник'
        lines.append(f'{who}: {truncate(m["text"], 300)}')
    lines.append(f'Собеседник только что написал: {truncate(text, 500)}')
    lines.append('Ответь на это сообщение.')
    return '\n'.join(lines)


async def _check_lock(uid):
    """Lock: отвечаем только контактам/людям с общими чатами."""
    if not state.lock_enabled:
        return True
    try:
        me = await _me()
        if uid == me.id:
            return True
        cached = db.get_saved(f'_lock_cache_{uid}')
        if cached is None:
            cached = '0'
            try:
                contact = await client.get_entity(uid)
                if getattr(contact, 'contact', False):
                    cached = '1'
            except Exception:
                pass
            if cached == '0':
                try:
                    if await client.get_common_chats(uid):
                        cached = '1'
                except Exception:
                    pass
            db.set_saved(f'_lock_cache_{uid}', cached)
        return cached == '1'
    except Exception:
        return True


@client.on(events.NewMessage(func=lambda e: e.is_private and e.raw_text))
async def ai_private_middleware(event):
    text = (event.raw_text or '').strip()
    if not text or text.startswith('!') or text.startswith('/'):
        return
    peer_id = event.chat_id
    if not peer_id or peer_id <= 0:
        return
    sender_id = event.sender_id or 0
    # Без ключей не собираем личные сообщения в AI-историю: иначе бот
    # хранит чужие тексты, хотя AI-команды всё равно недоступны. `!sync`
    # и `!style` пишут историю явно по команде.
    if AI_ENABLED:
        _poll_bump(peer_id)
        try:
            db.save_ai_msg(peer_id, sender_id, text[:1000], msg_id=event.id)
        except Exception as ex:
            logger.debug(f'AI save: {ex}')

    if not AI_ENABLED or event.out:
        return
    try:
        sender = await event.get_sender()
    except Exception:
        sender = None
    if not sender or getattr(sender, 'bot', False):
        return
    uid = event.sender_id
    if not uid or uid == OWNER_ID:
        return
    if state.cover_enabled or state.silent_enabled or state.mute_enabled:
        return
    # Обычный автоответчик/AFK уже ответил — не дублируем.
    if state.auto_reply_enabled or state.afk_start_time:
        return
    if not await _check_lock(uid):
        return
    if db.is_ai_blocked(uid):
        return
    whitelist = db.get_ai_list('white')
    if whitelist and uid not in whitelist:
        return
    auto = db.get_ai_auto(uid)
    if not auto['enabled']:
        return
    if _cooldown(f'autoreply_{uid}', AI_AUTO_COOLDOWN):
        return

    sysp = _system_for_auto(auto, uid)
    prompt = _user_prompt_response(uid, text)

    if db.get_ai_prompt_mode(uid):
        try:
            variants, prov = await _gen(
                prompt + '\n\nДополнительно: предложи 3 коротких варианта ответа, каждый на новой строке: 1), 2), 3).',
                system=sysp,
            )
            await _send_owner(
                ui.card('💬 Варианты ответа',
                        f'Для: {_target_name(sender)}\n\n{truncate(_ai_plain(variants), 2500)}',
                        footer=f'Провайдер: {prov}')
            )
        except AIError as ex:
            await _send_owner(ui.err(str(ex), title='AI prompt mode'))
        return

    if state.reply_delay:
        await asyncio.sleep(min(state.reply_delay, 30))
    try:
        async with client.action(event.chat_id, 'typing'):
            await asyncio.sleep(0.5 + random.uniform(0.4, 1.2))
    except Exception:
        pass

    try:
        answer, prov = await _gen(prompt, system=sysp)
    except AIError as ex:
        await _send_owner(ui.err(str(ex), title=f'Автоответ · {_target_name(sender)}'))
        return

    sent_msgs = []
    try:
        for chunk in _split_text(answer, 4000):
            sent_msgs.append(await event.reply(chunk))
        if state.ghost_mode or state.shadow_enabled or state.autodel_enabled:
            delay = state.autodel_delay if state.autodel_enabled else state.shadow_delay
            for sent in sent_msgs:
                asyncio.create_task(_shadow_delete(sent, delay))
        logger.info(f'🤖 AI автоответ ({prov}) → {uid}')
        db.bump_stat('cmds')
    except Exception as ex:
        logger.warning(f'AI auto reply send: {ex}')


async def _shadow_delete(msg, delay):
    from helpers import shadow_delete_msg
    await shadow_delete_msg(msg, delay)


# ────────────────────────────────────────────────────────────
# Реакции-триггеры (сервер не присылает свои реакции — короткий опрос)
# ────────────────────────────────────────────────────────────

REACTION_PROMPTS = {
    '🤔': 'Проанализируй текст и найди скрытый смысл, подтекст или манипуляцию: «{}»',
    '🤬': 'Проанализируй текст на токсичность, пассивную агрессию и «красные флаги»: «{}»',
    '⚡': 'Сделай краткую выжимку (TL;DR) сообщения в 1–2 предложениях: «{}»',
    '🤓': 'Переведи текст на русский (или на английский, если он уже на русском): «{}»',
    '🤡': 'Сделай жёсткий, но смешной и саркастичный роаст сообщения: «{}»',
}

_reaction_done = set()
_reaction_poll_started = False


def _poll_bump(peer_id):
    if not AI_REACTIONS or not AI_ENABLED:
        return
    _active_privates[peer_id] = time.time()
    if len(_active_privates) > 40:
        cutoff = time.time() - 600
        for k in [k for k, v in _active_privates.items() if v < cutoff]:
            _active_privates.pop(k, None)
    _ensure_reaction_poll()


def _ensure_reaction_poll():
    global _reaction_poll_started
    if _reaction_poll_started or not AI_REACTIONS:
        return
    _reaction_poll_started = True
    try:
        asyncio.create_task(_reaction_poll_loop())
        logger.info('[AI] Реакции: короткий опрос активен (каждые ~25 с)')
    except RuntimeError:
        _reaction_poll_started = False


async def _reaction_poll_loop():
    while True:
        try:
            await _reaction_poll_cycle()
        except asyncio.CancelledError:
            return
        except Exception as ex:
            logger.debug(f'AI reaction poll: {ex}')
        await asyncio.sleep(25)


def _own_reaction_emojis(reactions):
    out = set()
    results = getattr(reactions, 'results', None)
    if isinstance(results, (list, tuple)):
        for rc in results:
            if getattr(rc, 'chosen_order', None) is None:
                continue
            em = getattr(getattr(rc, 'reaction', None), 'emoticon', None)
            if em:
                out.add(em)
    return out


def _reaction_add(peer_id, msg_id, emojis):
    new = set()
    for em in emojis:
        key = (peer_id, msg_id, em)
        if key not in _reaction_done:
            _reaction_done.add(key)
            new.add(em)
    if len(_reaction_done) > 4000:
        for key in list(_reaction_done)[:3000]:
            _reaction_done.discard(key)
    return new


async def _process_reaction(peer_id, msg_id, emoji, text):
    prompt = REACTION_PROMPTS.get(emoji, REACTION_PROMPTS['🤔'])
    try:
        answer, prov = await _gen(
            prompt.replace('{}', truncate(text, 2000)),
            system='Ты — опытный аналитик. Отвечай кратко и по делу.',
        )
        quoted = truncate(text, 100)
        await _send_owner(
            ui.card(
                f'🔍 Разбор реакции {emoji}',
                f'💬 Сообщение: «{quoted}»\n\n💡 Ответ AI:\n{truncate(answer, 2500)}',
                footer=f'Провайдер: {prov}',
            )
        )
        logger.info(f'🤖 AI реакция {emoji} → {peer_id} (#{msg_id})')
    except AIError as ex:
        await _send_owner(ui.err(str(ex), title=f'Триггер {emoji}'))
    except Exception as ex:
        logger.warning(f'AI trigger {emoji}: {ex}')


async def _react_to_emojis(peer_id, msg_id, emojis):
    if not emojis:
        return
    try:
        msg = await client.get_messages(peer_id, ids=msg_id)
        if not msg or msg.out:
            return
        text = (msg.raw_text or '').strip()
    except Exception as ex:
        logger.debug(f'AI reaction fetch: {ex}')
        return
    if not text:
        return
    for emoji in emojis:
        asyncio.create_task(_process_reaction(peer_id, msg_id, emoji, text))


def _reaction_counts(update):
    raw = getattr(update, 'reactions', None)
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raw = (getattr(raw, 'results', None) or getattr(raw, 'reactions', None)) or []
    return list(raw)


async def _reactions_plural(update):
    if not AI_REACTIONS:
        return
    peer = getattr(update, 'peer', None)
    if not isinstance(peer, PeerUser) or peer.user_id <= 0:
        return
    _poll_bump(peer.user_id)
    active = set()
    for rc in _reaction_counts(update):
        if getattr(rc, 'chosen_order', None) is None:
            continue
        em = getattr(getattr(rc, 'reaction', None), 'emoticon', None)
        if em:
            active.add(em)
    new = _reaction_add(peer.user_id, update.msg_id, active & set(REACTION_PROMPTS))
    if new:
        await _react_to_emojis(peer.user_id, update.msg_id, new)


@client.on(events.Raw(types=UpdateMessageReactions))
async def reactions_user_plural(update):
    await _reactions_plural(update)


@client.on(events.Raw(types=UpdateBotMessageReactions))
async def reactions_bot_plural(update):
    await _reactions_plural(update)


@client.on(events.Raw(types=UpdateBotMessageReaction))
async def reactions_bot_single(update):
    if not AI_REACTIONS:
        return
    peer = getattr(update, 'peer', None)
    if not isinstance(peer, PeerUser) or peer.user_id <= 0:
        return
    actor = getattr(update, 'actor', None)
    if isinstance(actor, PeerUser) and getattr(actor, 'user_id', None) != OWNER_ID:
        return
    active = set()
    for r in getattr(update, 'new_reactions', None) or ():
        if isinstance(r, ReactionEmpty):
            continue
        em = getattr(r, 'emoticon', None)
        if em:
            active.add(em)
    new = _reaction_add(peer.user_id, update.msg_id, active & set(REACTION_PROMPTS))
    if new:
        await _react_to_emojis(peer.user_id, update.msg_id, new)


async def _reaction_poll_cycle():
    now = time.time()
    for peer_id in list(_active_privates):
        if now - _active_privates[peer_id] > 600:
            continue
        try:
            msgs = await client.get_messages(peer_id, limit=20)
        except Exception as ex:
            logger.debug(f'AI poll {peer_id}: {ex}')
            continue
        for msg in msgs or ():
            if not msg or getattr(msg, 'out', False):
                continue
            text = (msg.raw_text or '').strip()
            if not text or not getattr(msg, 'reactions', None):
                continue
            own = _own_reaction_emojis(msg.reactions)
            if not own:
                continue
            new = _reaction_add(peer_id, msg.id, own & set(REACTION_PROMPTS))
            for emoji in new:
                asyncio.create_task(_process_reaction(peer_id, msg.id, emoji, text))


# ────────────────────────────────────────────────────────────
# Команды: контекст и анализ
# ────────────────────────────────────────────────────────────

@client.on(events.NewMessage(pattern=r'!summary(?:\s+(@\w+|-?\d+))?(?:\s+(\d+))?$', func=owner_filter))
async def summary_cmd(e):
    mention, n = e.pattern_match.group(1), e.pattern_match.group(2)
    target = await _resolve_target(e, mention)
    if not target:
        await respond(e, ui.usage(
            '!summary @ник [n]',
            title='Выжимка переписки',
        ) + '\n💡 Работает в ЛС или ответом на сообщение.')
        return
    n = min(int(n or 20), AI_MAX_HISTORY)
    hist = db.get_ai_msgs(target.id, limit=n)
    if len(hist) < 3:
        await respond(e, ui.err(f'Мало сообщений в базе ({len(hist)}).',
                                title='Выжимка'))
        await e.reply(f'Сначала: `!sync @{getattr(target, "username", None) or target.id} {n}`',
                      parse_mode='md')
        return
    me = await _me()
    text = transcript(hist, me.id, 200)
    msg = await _status(e, '⏳ Выжимаю суть...')
    try:
        answer, prov = await _gen(
            'Сделай TL;DR переписки. Ответь ТОЛЬКО JSON без пояснений и разметки: '
            '{"who":"кто собеседник","about":"о чём речь",'
            '"direction":"куда движется разговор","conclusion":"вывод"}. '
            'Каждое значение — одно короткое предложение:\n\n' + text,
            system='Ты делаешь краткие точные выжимки. Возвращаешь только валидный JSON.',
            json_mode=True,
        )
        await _finish(msg, _ai_result(
            f'📌 Выжимка · {_target_name(target)}', answer, prov, render_summary,
            meta=f'{len(hist)} сообщений'),
            chat_id=e.chat_id)
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!vibe(?:\s+(@\w+|-?\d+))?$', func=owner_filter))
async def vibe_cmd(e):
    target = await _resolve_target(e, e.pattern_match.group(1))
    if not target:
        await respond(e, ui.usage('!vibe @ник', title='Настроение собеседника'))
        return
    hist = await _grab_history(target.id, 100)
    if len(hist) < 3:
        await respond(e, ui.err(f'Мало сообщений ({len(hist)}). Напишите друг другу или сделай `!sync`.',
                                title='Настроение'))
        return
    me = await _me()
    name = _target_name(target)
    msg = await _status(e, '⏳ Считываю настроение...')
    try:
        answer, prov = await _gen(
            f'Проанализируй настроение собеседника {name} по переписке. '
            'Ответь ТОЛЬКО валидным JSON без пояснений и разметки по схеме:\n'
            + _VIBE_SCHEMA + '\n'
            'Правила: scores — целые числа от 0 до 100 (0 = нет признака, '
            '100 = максимум), не строки и не «высокий». '
            'Приведи 2-3 доказательства дословными цитатами из переписки — '
            'не выдумывай цитаты и не пересказывай их своими словами. '
            'dynamics: 2-3 этапа с изменением настроения. '
            'triggers и red_flags: по 1-3 коротких пункта или пустые массивы, '
            'если данных нет. Не делай медицинских выводов. После JSON ничего не пиши.\n\n'
            'Переписка (Я — владелец, Он(а) — собеседник):\n'
            + transcript_dated(hist[-60:], me.id, 160),
            system='Ты — внимательный аналитик эмоций. Возвращаешь только валидный JSON '
                   'без Markdown и комментариев.',
            json_mode=True,
        )
        await _finish(msg, _ai_result(
            f'🎭 Настроение · {name}', answer, prov, render_vibe,
            meta=f'{len(hist)} сообщений'),
            chat_id=e.chat_id)
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None, chat_id=e.chat_id)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!liar(?:\s+(@\w+|-?\d+))?$', func=owner_filter))
async def liar_cmd(e):
    target = await _resolve_target(e, e.pattern_match.group(1))
    if not target:
        await respond(e, ui.usage('!liar @ник', title='Поиск противоречий'))
        return
    hist = await _grab_history(target.id, min(AI_MAX_HISTORY, 150))
    if len(hist) < 2:
        await respond(e, ui.err(f'Мало сообщений ({len(hist)}).', title='Противоречия'))
        return
    name = _target_name(target)
    me = await _me()
    facts = db.get_ai_facts(target.id)
    extra = ('\nИзвестные факты о человеке (сверяй с ними, но не выдавай за цитаты):\n- '
             + '\n- '.join(facts)) if facts else ''
    msg = await _status(e, '⏳ Ищу противоречия...')
    try:
        answer, prov = await _gen(
            f'Найди противоречия в словах собеседника {name}. '
            'Ответь ТОЛЬКО валидным JSON без разметки по схеме:\n'
            + _LIAR_SCHEMA + '\n'
            'Правила: цитаты — дословно из переписки, с датой в контексте; '
            'не придумывай ни цитат, ни дат. Максимум 5 самых сильных противоречий, '
            'у каждого — severity и короткие вопросы. '
            'credibility — целое число 0..100: насколько согласованы слова человека. '
            'Если данных мало или contradictions пуст — всё равно верни verdict, '
            'заполни unverified и questions. Различай «противоречие» и «просто '
            'изменил решение»: второе помечай как низкую severity. '
            'После JSON ничего не пиши.\n\n'
            'Переписка с датами (Я — владелец, Он(а) — собеседник):\n'
            + transcript_dated(hist[-120:], me.id, 170) + extra,
            system='Ты — дотошный, но fairness-ориентированный детектор противоречий. '
                   'Возвращаешь только валидный JSON.',
            json_mode=True,
        )
        await _finish(msg, _ai_result(
            f'🕵️ Противоречия · {name}', answer, prov, render_liar,
            meta=f'{len(hist)} сообщений'),
            chat_id=e.chat_id)
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None, chat_id=e.chat_id)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!promises(?:\s+(all|done|@\w+|-?\d+))?(?:\s+(\d+))?$', func=owner_filter))
async def promises_cmd(e):
    arg, arg2 = e.pattern_match.group(1), e.pattern_match.group(2)
    if arg == 'done' and arg2:
        ok = db.mark_ai_promise(arg2, 'done', note='закрыто вручную')
        result = (ui.ok(f'Обещание #{arg2} закрыто.', title='Обещания') if ok else
                  ui.err(f'Обещание #{arg2} не найдено.', title='Обещания'))
        await respond(e, result)
        db.bump_stat('cmds')
        return
    if arg == 'all':
        rows = db.get_ai_promises(status=None, limit=100)
        body = render_promises(rows, summary='Архив обещаний', show_status=True)
        await respond(e, compose_card('📜 Обещания · архив', body,
                                      meta=f'{len(rows)} записей'), parse_mode='html')
        db.bump_stat('cmds')
        return
    target = await _resolve_target(e, arg if arg not in ('all', 'done') else None)
    if not target:
        await respond(e, ui.usage(
            '!promises @ник | !promises done <id> | !promises all',
            title='Обещания'))
        return
    hist = await _grab_history(target.id, 150)
    if len(hist) < 2:
        await respond(e, ui.err(f'Мало сообщений ({len(hist)}).', title='Обещания'))
        return
    name, me = _target_name(target), await _me()
    open_rows = db.get_ai_promises(target.id, status='open', limit=100)
    tracking = '\n'.join(
        f'#{r["id"]} [{r["who_label"] or "?"}] {r["text"]} '
        f'(срок: {r["due_at"] or "не указан"})' for r in open_rows) or 'нет'
    msg = await _status(e, '⏳ Проверяю обещания...')
    try:
        answer, prov = await _gen(
            f'Проверь обещания в переписке с {name}. Ответь ТОЛЬКО валидным JSON по схеме:\n'
            + _PROMISES_SCHEMA + '\n'
            'Правила: new — только реально новые обещания дословно; who — "я", '
            'если обещал владелец, иначе имя собеседника; fulfilled/cancelled — '
            'id из списка отслеживаемых и только при явном подтверждении в '
            'переписке. Не выдумывай id и цитаты. Нет подтверждения — не закрывай. '
            'После JSON ничего не пиши.\n\n'
            f'Уже отслеживаемые открытые обещания:\n{tracking}\n\n'
            'Переписка с датами:\n' + transcript_dated(hist[-120:], me.id, 170),
            system='Ты — аккуратный аудитор обещаний. Возвращаешь только валидный JSON.',
            json_mode=True,
        )
        parsed = parse_ai_json(answer)
        data = parsed if isinstance(parsed, dict) else {}
        new_items = []
        for item in data.get('new') or []:
            if not isinstance(item, dict):
                continue
            who = str(item.get('who') or '').strip().lower()
            promiser = me.id if who in ('я', 'владелец', 'owner', 'вы') else target.id
            new_items.append({
                'text': item.get('text') or '',
                'who_label': 'вы' if promiser == me.id else name,
                'promiser_id': promiser,
                'due_at': item.get('due') or '',
                'note': item.get('note') or '',
            })
        added = db.save_ai_promises(target.id, new_items)
        for pid in data.get('fulfilled') or []:
            db.mark_ai_promise(pid, 'done', note='подтверждено в переписке')
        for pid in data.get('cancelled') or []:
            db.mark_ai_promise(pid, 'cancelled', note='снято в переписке')
        rows = db.get_ai_promises(target.id, status='open', limit=100)
        body = render_promises(rows, summary=data.get('summary') or '')
        await _finish(msg, compose_card(
            f'📜 Обещания · {name}', body, prov,
            meta=f'{len(rows)} открытых · +{added} новых · {len(hist)} сообщений'),
            chat_id=e.chat_id)
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None, chat_id=e.chat_id)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!mediation(?:\s+(@\w+|-?\d+))?(?:\s+(мягко|жёстко))?$', func=owner_filter))
async def mediation_cmd(e):
    mention, tone = e.pattern_match.group(1), e.pattern_match.group(2)
    target = await _resolve_target(e, mention)
    if not target:
        await respond(e, ui.usage('!mediation @ник [мягко|жёстко]', title='Примирение'))
        return
    hist = await _grab_history(target.id, 100)
    if len(hist) < 3:
        await respond(e, ui.err(f'Мало сообщений ({len(hist)}).', title='Примирение'))
        return
    name, me = _target_name(target), await _me()
    facts = db.get_ai_facts(target.id)
    extra = ('\nФакты о человеке:\n- ' + '\n- '.join(facts)) if facts else ''
    open_rows = db.get_ai_promises(target.id, status='open', limit=20)
    if open_rows:
        extra += '\nНевыполненные обещания:\n- ' + '\n- '.join(
            r['text'] for r in open_rows)
    msg = await _status(e, '⏳ Готовлю план примирения...')
    try:
        answer, prov = await _gen(
            f'Помоги владельцу безопасно помириться с {name}. Стиль: {tone or "мягко"}. '
            'Ответь ТОЛЬКО валидным JSON по схеме:\n' + _MEDIATION_SCHEMA + '\n'
            'Правила: diagnosis — нейтрально, без ярлыков; steps — 2-4 конкретных '
            'фразы от владельца; boundaries — что не обещать; не предлагай '
            'манипуляций, давления и клинических диагнозов. После JSON ничего не пиши.\n\n'
            'Переписка с датами:\n' + transcript_dated(hist[-80:], me.id, 170) + extra,
            system='Ты помогаешь спокойно договориться. Не диагностируй и не подталкивай к давлению. Возвращаешь только валидный JSON.',
            json_mode=True,
        )
        await _finish(msg, _ai_result(
            f'🕊 Примирение · {name}', answer, prov, render_mediation,
            meta=f'{len(hist)} сообщений'),
            chat_id=e.chat_id)
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None, chat_id=e.chat_id)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!reality(?:\s+(@\w+|-?\d+))?$', func=owner_filter))
async def reality_cmd(e):
    target = await _resolve_target(e, e.pattern_match.group(1))
    if not target:
        await respond(e, ui.usage('!reality @ник', title='Факты и трактовки'))
        return
    hist = await _grab_history(target.id, 100)
    if len(hist) < 3:
        await respond(e, ui.err(f'Мало сообщений ({len(hist)}).', title='Факты'))
        return
    name, me = _target_name(target), await _me()
    msg = await _status(e, '⏳ Отделяю факты от догадок...')
    try:
        answer, prov = await _gen(
            f'Разбери переписку с {name} и отдели подтверждённые факты от '
            'интерпретаций. Ответь ТОЛЬКО валидным JSON по схеме:\n'
            + _REALITY_SCHEMA + '\n'
            'Правила: facts — только дословные цитаты с датами; interpretations — '
            'что человек мог иметь в виду и какое есть альтернативное объяснение. '
            'Не выдумывай цитаты и не объявляй интерпретацию фактом. После JSON '
            'ничего не пиши.\n\nПереписка:\n'
            + transcript_dated(hist[-100:], me.id, 170),
            system='Ты отделяешь наблюдаемое от домыслов. Возвращаешь только валидный JSON.',
            json_mode=True,
        )
        await _finish(msg, _ai_result(
            f'⚖️ Факты и трактовки · {name}', answer, prov, render_reality,
            meta=f'{len(hist)} сообщений'),
            chat_id=e.chat_id)
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None, chat_id=e.chat_id)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!roles(?:\s+(@\w+|-?\d+))?$', func=owner_filter))
async def roles_cmd(e):
    mention = e.pattern_match.group(1)
    if mention:
        target = await resolve_peer(mention)
    elif e.reply_to_msg_id:
        replied = await e.get_reply_message()
        target = (await resolve_peer(str(replied.sender_id))
                  if replied and replied.sender_id else None)
    else:
        target = await e.get_chat()
    if not target:
        await respond(e, ui.usage('!roles @ник|ID|чат', title='Роли в чате'))
        return
    hist = await _grab_history(target.id, 150)
    if len(hist) < 5:
        await respond(e, ui.err(f'Мало сообщений ({len(hist)}).', title='Роли'))
        return
    me = await _me()
    counts = {}
    for m in hist:
        sid = m.get('sender_id')
        if sid is not None:
            counts[sid] = counts.get(sid, 0) + 1
    top = sorted(counts, key=counts.get, reverse=True)[:6]
    labels = {}
    for sid in top:
        if sid == me.id:
            labels[sid] = 'вы'
        elif sid == target.id:
            labels[sid] = _target_name(target)
        else:
            try:
                labels[sid] = _target_name(await client.get_entity(sid))
            except Exception:
                labels[sid] = str(sid)
    roster = '\n'.join(f'{labels[s]} — {counts[s]} сообщений' for s in top)
    lines = []
    for m in hist[-120:]:
        text = truncate((m.get('text') or '').strip(), 170)
        if not text:
            continue
        stamp = timestamp_label(m.get('date'))
        prefix = f'{stamp} ' if stamp else ''
        lines.append(f'[{prefix}{labels.get(m.get("sender_id"), "?")}] {text}')
    msg = await _status(e, '⏳ Определяю роли...')
    try:
        answer, prov = await _gen(
            'Определи поведенческие роли участников переписки. Ответь ТОЛЬКО '
            'валидным JSON по схеме:\n' + _ROLES_SCHEMA + '\n'
            'Правила: оцени ВСЕХ участников из списка, включая "вы". confidence — '
            'целое 0..100, высокие значения только при цитатах-доказательствах; '
            'без цитат — не выше 40. Не используй клинические диагнозы и не '
            'называй кого-то «жертвой» как факт — описывай поведение в переписке. '
            'Не придумывай цитаты. После JSON ничего не пиши.\n\n'
            'Участники:\n' + roster + '\n\nПереписка:\n' + '\n'.join(lines),
            system='Ты анализируешь поведение в переписке. Это не диагноз и не моральный приговор. Возвращаешь только валидный JSON.',
            json_mode=True,
        )
        data = parse_ai_json(answer)
        body = render_roles(data, samples=len(hist)) if isinstance(data, dict) else ''
        if not body.strip():
            body = md_to_telegram_html(answer)
        await _finish(msg, compose_card(
            f'🎭 Роли · {_target_name(target)}', body, prov,
            meta=f'{len(hist)} сообщений'),
            chat_id=e.chat_id)
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None, chat_id=e.chat_id)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!sync(?:\s+(@\w+|-?\d+))?(?:\s+(\d+))?$', func=owner_filter))
async def sync_cmd(e):
    mention, n = e.pattern_match.group(1), e.pattern_match.group(2)
    target = await _resolve_target(e, mention)
    if not target:
        await respond(e, ui.usage('!sync @ник [кол-во]', title='Синхронизация истории'))
        return
    n = min(int(n or 100), 500)
    msg = await _status(e, f'⏳ Синхронизирую {n} сообщений с {_target_name(target)}...')
    saved = 0
    try:
        async for m in client.iter_messages(target.id, limit=n):
            text = (m.raw_text or '').strip()
            if not text or text.startswith('!'):
                continue
            db.save_ai_msg(
                target.id, m.sender_id or target.id, text[:1000],
                ts=m.date.timestamp() if m.date else time.time(),
                msg_id=m.id,
            )
            saved += 1
            if saved % 50 == 0:
                await asyncio.sleep(0.2)
    except Exception as ex:
        await _finish(msg, f'❌ Не удалось синхронизировать: {ex}', parse_mode=None)
        return
    await _finish(msg, f'✅ Синхронизировано: {saved} сообщений с {_target_name(target)}.', parse_mode=None)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!fact(?:\s+(.+))?$', func=owner_filter))
async def fact_cmd(e):
    arg = (e.pattern_match.group(1) or '').strip()
    if not arg:
        await respond(e, ui.usage('!fact @ник факт', '!fact clear @ник', title='Факты'))
        return
    if arg.lower() == 'clear' or arg.lower().startswith('clear '):
        rest = arg[5:].strip()
        target = await _resolve_target(e, rest or None)
        if not target:
            await respond(e, ui.usage('!fact clear @ник|ID', title='Очистка фактов'))
            return
        db.clear_ai_facts(target.id)
        await respond(e, ui.ok(f'Факты о {_target_name(target)} удалены.', title='Факты'))
        db.bump_stat('cmds')
        return
    parts = arg.split(None, 1)
    if len(parts) < 2:
        await respond(e, ui.usage('!fact @ник текст факта', title='Факты'))
        return
    try:
        target = await resolve_peer(parts[0], positive_only=True)
    except Exception:
        await respond(e, ui.err(f'Не нашёл: `{parts[0]}`', title='Факты'), parse_mode='md')
        return
    db.add_ai_fact(target.id, truncate(parts[1], 300))
    facts = db.get_ai_facts(target.id)
    await respond(e, ui.ok(f'Факт сохранён о {_target_name(target)}. Всего: {len(facts)}.',
                           title='Факты'))
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!facts(?:\s+(@\w+|-?\d+))?$', func=owner_filter))
async def facts_cmd(e):
    target = await _resolve_target(e, e.pattern_match.group(1))
    if not target:
        await respond(e, ui.usage('!facts @ник|ID', title='Факты о человеке'))
        return
    facts = db.get_ai_facts(target.id)
    if not facts:
        await respond(e, ui.info(f'Фактов о {_target_name(target)} пока нет.',
                                 title='Факты'))
    else:
        body = '\n'.join(f'• {f}' for f in facts)
        await respond(e, ui.card(f'🧠 Факты · {_target_name(target)}', body))
    db.bump_stat('cmds')


# ────────────────────────────────────────────────────────────
# Команды: редактура
# ────────────────────────────────────────────────────────────

async def _edit_target(e):
    text, err = await _last_text(e)
    if not text:
        return None, ('Нет текста. Ответь на сообщение, передай текст аргументом '
                      'или используй в ЛС.')
    return text, None


@client.on(events.NewMessage(pattern=r'!rewrite(?:\s+(.+))?$', func=owner_filter))
async def rewrite_cmd(e):
    style = (e.pattern_match.group(1) or '').strip() or 'вежливо'
    text, err = await _edit_target(e)
    if err:
        await respond(e, ui.err(err, title='Редактура'))
        return
    msg = await _status(e, f'⏳ Переписываю в стиле «{style}»...')
    try:
        answer, prov = await _gen(
            f'Перепиши сообщение в стиле «{style}», сохранив смысл. '
            f'Верни только готовый текст:\n\n{truncate(text, 2000)}',
            system='Ты — редактор текста.',
        )
        await _finish(msg, _ai_result(f'✍️ Переписано · {style}', answer, prov))
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!fix(?:\s+(.+))?$', func=owner_filter))
async def fix_cmd(e):
    raw = (e.pattern_match.group(1) or '').strip()
    if raw:
        text = raw
    else:
        text, err = await _edit_target(e)
        if err:
            await respond(e, ui.err(err, title='Редактура'))
            return
    swapped = swap_layout(text)
    msg = await _status(e, '⏳ Правлю...')
    try:
        answer, prov = await _gen(
            'Исправь грамматику, опечатки и раскладку текста. '
            f'Верни только исправленный текст:\n\n{truncate(swapped, 2000)}',
            system='Ты — редактор: верни только итоговый текст без объяснений.',
        )
        await _finish(msg, _ai_result('🔧 Исправлено', answer, prov))
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!factcheck$', func=owner_filter))
async def factcheck_cmd(e):
    text, err = await _edit_target(e)
    if err:
        await respond(e, ui.err(err, title='Фактчек'))
        return
    msg = await _status(e, '⏳ Проверяю факты...')
    try:
        answer, prov = await _gen(
            'Проверь факты и нестыковки в утверждениях. Где сомнительно — отметь и предложи, '
            f'как проверить. Ответь кратко:\n\n{truncate(text, 2500)}',
            system='Ты — фактчекер. Подчёркивай степень уверенности каждого пункта.',
        )
        await _finish(msg, _ai_result('🔍 Фактчек', answer, prov))
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!args$', func=owner_filter))
async def args_cmd(e):
    text, err = await _edit_target(e)
    if err:
        await respond(e, ui.err(err, title='Контраргументы'))
        return
    msg = await _status(e, '⏳ Куём контраргументы...')
    try:
        answer, prov = await _gen(
            'Дай 3 железных контраргумента на утверждение — логичных, с фактами и без эмоций. '
            f'Нумеруй:\n\n{truncate(text, 2000)}',
            system='Ты — мастер дебатов.',
        )
        await _finish(msg, _ai_result('⚔️ Контраргументы', answer, prov))
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!tr(?:\s+([a-z]{2}))?$', func=owner_filter))
async def tr_cmd(e):
    lang = (e.pattern_match.group(1) or 'ru').lower()
    text, err = await _edit_target(e)
    if err:
        await respond(e, ui.err(err, title='Перевод'))
        return
    msg = await _status(e, f'⏳ Перевожу на {lang}...')
    try:
        answer, prov = await _gen(
            f'Переведи на язык с кодом {lang}. Только перевод, без пояснений:\n\n'
            f'{truncate(text, 2500)}',
            system='Ты — профессиональный переводчик.',
        )
        await _finish(msg, _ai_result(f'🌐 Перевод · {lang}', answer, prov))
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None)
    db.bump_stat('cmds')


# ────────────────────────────────────────────────────────────
# Команды: медиа (OCR / TTS / кружок)
# ────────────────────────────────────────────────────────────

@client.on(events.NewMessage(pattern=r'!ocr$', func=owner_filter))
async def ocr_cmd(e):
    if not e.reply_to_msg_id:
        await respond(e, ui.usage('!ocr (ответом на картинку)', title='Распознавание текста'))
        return
    replied = await e.get_reply_message()
    if not replied or not replied.media:
        await respond(e, ui.err('В ответном сообщении нет картинки.', title='OCR'))
        return
    msg = await _status(e, '⏳ Распознаю текст...')
    os.makedirs(MEDIA_DIR, exist_ok=True)
    path = os.path.join(MEDIA_DIR, f'ai_ocr_{int(time.time() * 1000)}')
    downloaded = None
    try:
        downloaded = await replied.download_media(file=path)
        if not downloaded:
            await _finish(msg, '❌ Не удалось скачать картинку.', parse_mode=None)
            return
        try:
            size = os.path.getsize(downloaded)
            if size > 15 * 1024 * 1024:
                await _finish(msg, '❌ Картинка больше 15 МБ.', parse_mode=None)
                return
            with open(downloaded, 'rb') as f:
                image = f.read()
        except OSError as ex:
            await _finish(msg, f'❌ Ошибка чтения файла: {ex}', parse_mode=None)
            return
        answer, prov = await _gen(
            'Распознай весь текст с изображения. Если текста нет — так и напиши. '
            'Верни только распознанный текст.',
            system='Ты — OCR-движок.',
            image=image,
        )
        await _finish(msg, _ai_result('📄 Распознанный текст', answer, prov))
    except AIError as ex:
        await _finish(msg, f'❌ {ex}', parse_mode=None)
    except Exception as ex:
        logger.warning(f'ocr: {ex}')
        await _finish(msg, f'❌ Ошибка OCR: {ex}', parse_mode=None)
    finally:
        for p in (path, downloaded):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!tts(?:\s+(.+))?$', func=owner_filter))
async def tts_cmd(e):
    text = (e.pattern_match.group(1) or '').strip()
    if not text:
        text, err = await _edit_target(e)
        if err:
            await respond(e, ui.err(err, title='Озвучка'))
            return
    if len(text) > 4000:
        await respond(e, ui.err('Текст длиннее 4000 символов — сократи.', title='Озвучка'))
        return
    msg = await _status(e, '⏳ Озвучиваю...')
    stamp = int(time.time() * 1000)
    mp3 = os.path.join(tempfile.gettempdir(), f'ai_tts_{stamp}.mp3')
    ogg = os.path.join(tempfile.gettempdir(), f'ai_tts_{stamp}.ogg')
    try:
        try:
            import edge_tts
        except ImportError:
            await _finish(msg, '❌ edge-tts не установлен: `pip install edge-tts`', parse_mode=None)
            return
        await edge_tts.Communicate(text, AI_TTS_VOICE).save(mp3)
        if not os.path.exists(mp3):
            await _finish(msg, '❌ Не удалось синтезировать голос.', parse_mode=None)
            return
        ffmpeg = shutil.which('ffmpeg')
        voice = False
        path = mp3
        if ffmpeg:
            proc = await asyncio.to_thread(
                subprocess.run,
                [ffmpeg, '-y', '-i', mp3, '-c:a', 'libopus', '-b:a', '48k',
                 '-ar', '48000', ogg],
                capture_output=True, timeout=120,
            )
            if proc.returncode == 0 and os.path.exists(ogg):
                path, voice = ogg, True
        await client.send_file(e.chat_id, path, voice_note=voice, reply_to=getattr(msg, 'id', None))
        try:
            await msg.delete()
        except Exception:
            pass
        logger.info(f'🎙 AI TTS → {e.chat_id}')
    except Exception as ex:
        logger.warning(f'tts: {ex}')
        await _finish(msg, f'❌ Ошибка TTS: {ex}', parse_mode=None)
    finally:
        for p in (mp3, ogg):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!circle$', func=owner_filter))
async def circle_cmd(e):
    if not e.reply_to_msg_id:
        await respond(e, ui.usage('!circle (ответом на видео/гифку)', title='Кружочек'))
        return
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        await respond(e, ui.err('ffmpeg не установлен — кружочек недоступен.', title='Кружочек'))
        return
    replied = await e.get_reply_message()
    if not replied or not replied.media:
        await respond(e, ui.err('В ответном сообщении нет медиа.', title='Кружочек'))
        return
    msg = await _status(e, '⏳ Конвертирую в кружочек...')
    os.makedirs(MEDIA_DIR, exist_ok=True)
    stamp = int(time.time() * 1000)
    src = os.path.join(MEDIA_DIR, f'ai_circle_src_{stamp}')
    out = os.path.join(MEDIA_DIR, f'ai_circle_{stamp}.mp4')
    downloaded = None
    try:
        downloaded = await replied.download_media(file=src)
        if not downloaded:
            await _finish(msg, '❌ Не удалось скачать медиа.', parse_mode=None)
            return
        base = [ffmpeg, '-y', '-t', '60', '-i', downloaded,
                '-vf', "crop='min(iw,ih)':min(iw,ih),scale=512:512,setsar=1",
                '-pix_fmt', 'yuv420p', '-an']
        encoders = (
            ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28'],
            ['-c:v', 'libopenh264', '-b:v', '900k'],
            ['-c:v', 'mpeg4', '-q:v', '5'],
        )
        last_err = ''
        ok = False
        for enc in encoders:
            proc = await asyncio.to_thread(
                subprocess.run,
                base + enc + ['-movflags', '+faststart', out],
                capture_output=True, timeout=180,
            )
            if proc.returncode == 0 and os.path.exists(out):
                ok = True
                break
            last_err = (proc.stderr or b'').decode('utf-8', errors='replace')[-300:]
            try:
                os.remove(out)
            except OSError:
                pass
        if not ok:
            await _finish(msg, f'❌ Конвертация не удалась: {last_err}', parse_mode=None)
            return
        await client.send_file(e.chat_id, out, video_note=True, reply_to=getattr(msg, 'id', None))
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception as ex:
        logger.warning(f'circle: {ex}')
        await _finish(msg, f'❌ Ошибка: {ex}', parse_mode=None)
    finally:
        for p in (src, out, downloaded):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
    db.bump_stat('cmds')


# ────────────────────────────────────────────────────────────
# Команды: автоответчик и доступ
# ────────────────────────────────────────────────────────────

@client.on(events.NewMessage(pattern=r'!auto\s+(@\w+|-?\d+)\s+(on|off)(?:\s+(.+))?$', func=owner_filter))
async def auto_cmd(e):
    mention, mode, desc = e.pattern_match.group(1), e.pattern_match.group(2), e.pattern_match.group(3)
    try:
        target = await resolve_peer(mention, positive_only=True)
    except Exception:
        await respond(e, ui.err(f'Не нашёл: `{mention}`', title='Автоответчик'))
        return
    if mode == 'off':
        db.set_ai_auto(target.id, False)
        await respond(e, ui.ok(f'Автоответчик для {_target_name(target)} выключен.',
                               title='Автоответчик'))
    else:
        desc = (desc or 'друг').strip()
        db.set_ai_auto(target.id, True, prompt=desc)
        await respond(e, ui.ok(
            f'Автоответчик для {_target_name(target)} включён.\n'
            f'Собеседник: {desc}. Копирую твою манеру речи из этого чата.',
            title='Автоответчик'))
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!style\s+(@\w+|-?\d+)\s+on(?:\s+(\d+))?$', func=owner_filter))
async def style_on_cmd(e):
    mention, n = e.pattern_match.group(1), e.pattern_match.group(2)
    try:
        # Группа/канал тоже подходят: стиль можно взять из любого чата.
        src = await resolve_peer(mention)
    except Exception:
        await respond(e, ui.err(f'Не нашёл: `{mention}`', title='Стиль'))
        return
    n = max(5, min(int(n or 60), 400))
    msg = await _status(e, '🎨 Собираю твои сообщения для стиля...')
    # Заранее подтягиваем именно сообщения владельца: middleware пишет
    # только ЛС, поэтому иначе !style @группа не находил ни одной реплики.
    me = await _me()
    saved = 0
    try:
        async for m in client.iter_messages(src.id, limit=max(n * 2, 40)):
            text = (m.raw_text or '').strip()
            if m.sender_id != me.id or not text or text.startswith('!'):
                continue
            db.save_ai_msg(src.id, me.id, text[:1000],
                           ts=m.date.timestamp() if m.date else time.time(),
                           msg_id=m.id)
            saved += 1
            if saved >= n:
                break
    except Exception as ex:
        logger.debug(f'style ingest: {ex}')
    db.set_state('ai_style_src', str(src.id))
    db.set_state('ai_style_n', str(n))
    await _finish(msg, f'🎨 Стиль: {_target_name(src)}, последние {n} сообщений '
                      f'(собрано {saved}). Автоответчики копируют эту манеру.',
                  parse_mode=None)
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!style\s+off$', func=owner_filter))
async def style_off_cmd(e):
    db.set_state('ai_style_src', '')
    db.set_state('ai_style_n', '')
    await respond(e, ui.ok('Стиль-источник сброшен: снова беру манеру из текущего чата.',
                           title='Стиль'))
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!prompt\s+(@\w+|-?\d+)\s+(on|off)$', func=owner_filter))
async def prompt_cmd(e):
    mention, mode = e.pattern_match.group(1), e.pattern_match.group(2)
    try:
        target = await resolve_peer(mention, positive_only=True)
    except Exception:
        await respond(e, ui.err(f'Не нашёл: `{mention}`', title='Подсказки'))
        return
    db.set_ai_prompt_mode(target.id, mode == 'on')
    await respond(e, ui.ok(
        f'Режим подсказок для {_target_name(target)}: '
        f'{"включён — 3 варианта пришлю тебе" if mode == "on" else "выключен"}.',
        title='Подсказки'))
    db.bump_stat('cmds')


async def _list_cmd(e, list_type, title):
    mention = e.pattern_match.group(1)
    if mention:
        try:
            target = await resolve_peer(mention, positive_only=True)
        except Exception:
            await respond(e, ui.err(f'Не нашёл: `{mention}`', title=title))
            return
        added = db.toggle_ai_list(target.id, list_type)
        if added:
            other = 'white' if list_type == 'black' else 'black'
            if target.id in db.get_ai_list(other):
                db.toggle_ai_list(target.id, other)
        await respond(e, ui.ok(
            f'{_target_name(target)} {"добавлен в" if added else "убран из"} {title.lower()}.',
            title=title))
    else:
        ids = db.get_ai_list(list_type)
        if not ids:
            await respond(e, ui.info(f'{title} пуст.', title=title))
        else:
            lines = []
            for uid in ids[:50]:
                try:
                    ent = await resolve_peer(str(uid))
                    lines.append(f'• {_target_name(ent)} (`{uid}`)')
                except Exception:
                    lines.append(f'• `{uid}`')
            await respond(e, ui.card(f'{title} · {len(ids)}', '\n'.join(lines)))
    db.bump_stat('cmds')


@client.on(events.NewMessage(pattern=r'!blacklist(?:\s+(@\w+|-?\d+))?$', func=owner_filter))
async def blacklist_cmd(e):
    await _list_cmd(e, 'black', 'Чёрный список')


@client.on(events.NewMessage(pattern=r'!whitelist(?:\s+(@\w+|-?\d+))?$', func=owner_filter))
async def whitelist_cmd(e):
    await _list_cmd(e, 'white', 'Белый список')


# ────────────────────────────────────────────────────────────
# !helpai
# ────────────────────────────────────────────────────────────

_AI_HELP = {
    'контекст': (
        '👤 Контекст и анализ\n\n'
        '`!summary @ник [n]` — выжимка переписки в 3 предложениях\n'
        '`!vibe @ник` — настроение (агрессия/симпатия/вовлечённость)\n'
        '`!liar @ник` — противоречия по истории\n'
        '`!sync @ник [n]` — сохранить историю в базу\n'
        '`!fact @ник текст` · `!facts @ник` · `!fact clear @ник` — факты'
    ),
    'редактура': (
        '✏️ Редактура\n\n'
        '`!rewrite [стиль]` — переписать текст\n'
        '`!fix [текст]` — грамматика и раскладка\n'
        '`!factcheck` — проверка фактов и нестыковок\n'
        '`!args` — 3 контраргумента\n'
        '`!tr [язык]` — перевод'
    ),
    'медиа': (
        '🎙 Медиа\n\n'
        '`!ocr` — текст с картинки (ответом)\n'
        '`!tts [текст]` — голосовое (голос AI_TTS_VOICE)\n'
        '`!circle` — видео/гифка → кружочек (ответом)'
    ),
    'автоответчик': (
        '🤖 Автоответчик\n\n'
        '`!auto @ник on [кто он]` — включить, копируя твою манеру\n'
        '`!auto @ник off` — выключить\n'
        '`!style @чат on [N]` · `!style off` — источник манеры\n'
        '`!prompt @ник on|off` — присылать 3 варианта ответа\n'
        '`!blacklist @ник` · `!whitelist @ник` — доступ'
    ),
    'квест': (
        '🏰 Квест\n\n'
        '`!quest начать` — новая история в ЛС с собеседником\n'
        '`!quest [действие]` — ход текстом\n'
        '`!quest стоп` — остановить\n'
        'Кнопки-варианты приходят через Telegram Business от твоего имени.'
    ),
    'реакции': (
        '🎯 Реакции (разбор в отчёт владельцу)\n\n'
        'Поставь реакцию на входящее сообщение в ЛС:\n'
        '🤔 — скрытый смысл · 🤬 — токсичность · ⚡ — TL;DR\n'
        '🤓 — перевод · 🤡 — роаст'
    ),
}


@client.on(events.NewMessage(pattern=r'!helpai(?:\s+(.+))?$', func=owner_filter))
async def helpai_cmd(e):
    arg = (e.pattern_match.group(1) or '').strip().lower()
    if arg == 'all':
        body = '\n\n'.join(_AI_HELP.values())
        text = ('🤖 AI-помощник · все команды\n\n' + body)[:4080]
        await respond(e, text)
    elif arg in _AI_HELP:
        await respond(e, _AI_HELP[arg])
    elif arg:
        await respond(e, ui.err('Раздел не найден.', title='AI-справка',
                                hint='контекст · редактура · медиа · автоответчик · квест · реакции · all'))
    else:
        cats = '\n'.join(f'• `{c}`' for c in _AI_HELP)
        await respond(e, ui.card('🤖 AI-помощник',
                                 f'Разделы:\n{cats}',
                                 footer='`!helpai <раздел>` · `!helpai all`'))
    db.bump_stat('cmds')
