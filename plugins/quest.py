# -*- coding: utf-8 -*-
"""Интерактивный текстовый квест в ЛС с собеседником.

В отличие от Prim, где AI просто присылал абзац текста, здесь сцена
отправляется через Telegram Business от имени владельца прямо в чат, а
варианты действий — инлайн-кнопками. Нажатие обрабатывает внешний бот;
карточка редактируется на каждом ходу, поэтому в переписке остаётся один
игровой экран, а не лента сообщений.
"""

import json
import time

from telethon import events

from ai_engine import AIEngine, AIError
from ai_utils import parse_quest_scene, truncate
from client import client
from config import OWNER_ID, logger
from core import db, owner_filter, respond
from notifier import notify
import ui

eng = AIEngine()

_START = {'начать', 'новая', 'новый', 'старт', 'рестарт', 'start'}
_STOP = {'стоп', 'конец', 'выйти', 'stop', 'отмена'}

_SYSTEM = (
    'Ты — мастер текстового RPG-квеста в Telegram. Веди живую историю на русском, '
    'короткими сценами (3–5 предложений), с последствиями выборов. '
    'Верни СТРОГО JSON без пояснений и без Markdown: '
    '{"scene": "текст сцены", "options": ["вариант 1", "вариант 2", "вариант 3"]}. '
    'В options — 2–3 действия игрока, каждое до 60 символов, без нумерации.'
)


def _state(chat_id):
    raw = db.get_ai_quest(chat_id)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save(state):
    state['updated'] = int(time.time())
    db.set_ai_quest(state['peer_id'], json.dumps(state, ensure_ascii=False)[:8000])


def _card(state):
    step = state.get('step', 0)
    scene = truncate(state.get('scene') or 'История начинается...', 2800)
    body = f'{scene}\n\n**Твой ход:**'
    return ui.card(f'🏰 Квест · шаг {step}', body,
                   hint='Выбери вариант кнопкой ниже.')


def _buttons(chat_id, options):
    rows = []
    for i, opt in enumerate(options or ()):
        label = (opt or '').strip()[:60] or f'Вариант {i + 1}'
        rows.append([{'text': label, 'callback_data': f'q:{chat_id}:{i}'}])
    return rows


async def _deliver(state, buttons=None):
    """Отправить/обновить игровую карточку через Business.

    Возвращает (ok, error). Если карточка уже есть — редактируем её,
    иначе отправляем новую и запоминаем message_id.
    """
    chat_id = state['peer_id']
    html = ui.to_html(_card(state))
    biz = notify.business_connection_id
    same_chat = str(chat_id) == str(notify.chat_id)

    if not biz and not same_chat:
        return False, ('Нет активного Business-подключения: квест нельзя отправить '
                       'в этот чат от твоего имени. Подключи бота в Настройки → '
                       'Бизнес → Чатботы.')

    mid = state.get('msg_id')
    if mid:
        ok = await notify.edit(
            mid, html, buttons=buttons,
            business_connection_id=None if same_chat else biz,
            chat_id=None if same_chat else chat_id,
        )
        if ok:
            return True, None
        logger.info(f"[QUEST] edit не удался (chat={chat_id}, msg={mid}) — шлю новую карточку")
        # Гасим кнопки старой карточки: иначе два экрана спорят за ходы.
        try:
            await notify.edit(
                mid, '🏰 Квест продолжен ниже.', buttons=[],
                business_connection_id=None if same_chat else biz,
                chat_id=None if same_chat else chat_id,
            )
        except Exception as ex:
            logger.debug(f'[QUEST] clear old buttons: {ex}')

    kwargs = {'buttons': buttons, 'return_data': True}
    if not same_chat:
        kwargs['business_connection_id'] = biz
        kwargs['chat_id'] = chat_id
    data = await notify.send(html, **kwargs)
    if data and data.get('ok'):
        state['msg_id'] = (data.get('result') or {}).get('message_id')
        _save(state)
        return True, None
    return False, 'Не удалось отправить карточку квеста через внешнего бота.'


async def _turn(chat_id, action, state=None, start=False):
    """Сгенерировать следующий ход и обновить карточку."""
    if start or state is None:
        # Переиспользуем прошлую карточку: иначе в чате останется старый
        # экран с рабочими кнопками, и два квеста будут спорить за ходы.
        old = _state(chat_id) if start else None
        state = {
            'peer_id': chat_id,
            'owner_id': OWNER_ID,
            'step': 0,
            'story': '',
            'scene': '',
            'options': [],
            'msg_id': (old or {}).get('msg_id'),
        }

    story = truncate(state.get('story') or '', 2200)
    if start:
        prompt = (
            'Начни новый захватывающий квест. Придумай завязку, место и цель. '
            'Опиши стартовую сцену (3–5 предложений) и дай 2–3 варианта действий.\n'
            f'Прошлый сюжет (может быть пустым): {story or "—"}'
        )
    else:
        prompt = (
            f'Сюжет до этого:\n{story or "—"}\n\n'
            f'Игрок делает: «{truncate(action, 500)}». '
            'Опиши результат (3–5 предложений), последствия и дай 2–3 новых варианта.'
        )

    try:
        raw, prov = await eng.generate(prompt, system_prompt=_SYSTEM)
    except AIError as ex:
        return False, str(ex)

    scene, options = parse_quest_scene(raw)
    state['step'] = int(state.get('step') or 0) + 1
    state['scene'] = scene
    state['options'] = options
    state['last_action'] = truncate(action, 300) if action else 'Начало'
    state['story'] = truncate(
        (state.get('story') or '') + f'\nШаг {state["step"]}: {scene}', 2600)

    ok, err = await _deliver(state, _buttons(chat_id, options))
    if not ok:
        return False, err
    _save(state)
    logger.info(f'🏰 Квест: шаг {state["step"]} → {chat_id} ({prov})')
    return True, None


async def _notify_owner(text):
    """Ошибка квеста владельцу: внешний бот, иначе «Избранное»."""
    try:
        if notify.enabled and await notify.send(ui.to_html(ui.err(text, title='Квест'))):
            return
    except Exception:
        pass
    try:
        me = await client.get_me()
        await client.send_message(me.id, text)
    except Exception as ex:
        logger.debug(f'quest notify: {ex}')


async def _close_card(state):
    """Завершить квест: убрать кнопки и показать финал."""
    if not state or not state.get('msg_id'):
        return
    chat_id = state['peer_id']
    biz = notify.business_connection_id
    same_chat = str(chat_id) == str(notify.chat_id)
    text = ui.to_html(ui.card(
        '🏁 Квест завершён',
        truncate(state.get('scene') or '', 2500),
        hint='Партия завершена.',
    ))
    try:
        await notify.edit(
            state['msg_id'], text, buttons=[],
            business_connection_id=None if same_chat else biz,
            chat_id=None if same_chat else chat_id,
        )
    except Exception as ex:
        logger.debug(f'quest close: {ex}')


@client.on(events.NewMessage(pattern=r'!quest(?:\s+(.+))?$', func=owner_filter))
async def quest_cmd(e):
    if not e.is_private:
        await respond(e, ui.err('Квест работает только в личных сообщениях.',
                                title='Квест'))
        return
    chat_id = e.chat_id
    arg = (e.pattern_match.group(1) or '').strip()
    low = arg.lower()

    if low in _STOP:
        state = _state(chat_id)
        db.clear_ai_quest(chat_id)
        await _close_card(state)
        await respond(e, ui.ok('Квест остановлен.', title='Квест'))
        db.bump_stat('cmds')
        return

    state = _state(chat_id)
    start = low in _START or (not arg and state is None)
    if not arg and not start:
        await respond(e, ui.card(
            f'🏰 Квест · шаг {state.get("step", 0) if state else 0}',
            'Напиши действие: `!quest <что делаю>` — или выбери вариант кнопкой.',
            footer='`!quest стоп` — завершить',
        ))
        return
    msg = await respond(e, '🎮 Генерирую ход...', parse_mode=None)
    ok, err = await _turn(chat_id, arg or None, state=state, start=start)
    if not ok:
        try:
            if msg:
                await msg.edit(f'❌ {err}', parse_mode=None)
        except Exception:
            pass
        return
    try:
        if msg:
            await msg.delete()
    except Exception:
        pass
    db.bump_stat('cmds')


async def handle_quest_callback(query, data):
    """callback_data q:<chat_id>:<option_index> от внешнего бота."""
    qid = query.get('id')
    try:
        _, raw_chat, raw_idx = data.split(':', 2)
        chat_id, idx = int(raw_chat), int(raw_idx)
    except (ValueError, AttributeError):
        await notify.answer_callback(qid, 'Некорректный ход', alert=True)
        return

    state = _state(chat_id)
    if not state:
        await notify.answer_callback(qid, 'Квест не найден или уже завершён', alert=True)
        return

    uid = (query.get('from') or {}).get('id')
    allowed = {OWNER_ID, state.get('owner_id'), state.get('peer_id')}
    if uid not in allowed:
        await notify.answer_callback(qid, 'Нет доступа', alert=True)
        return

    options = state.get('options') or []
    if idx < 0 or idx >= len(options):
        await notify.answer_callback(qid, 'Этот вариант устарел — выбери актуальный',
                                     alert=True)
        return

    await notify.answer_callback(qid, 'Ход принят')
    action = options[idx]
    ok, err = await _turn(chat_id, action, state=state)
    if not ok:
        await _notify_owner(f'Ход не сгенерирован: {err}')
