# -*- coding: utf-8 -*-
"""Чистые помощники AI-команд: без Telethon, БД и .env (легко тестировать)."""

import json
import re

# Инверсия ЙЦУКЕН ↔ QWERTY: «ghbdtn» → «привет».
_QW = {
    'й': 'q', 'ц': 'w', 'у': 'e', 'к': 'r', 'е': 't', 'н': 'y', 'г': 'u',
    'ш': 'i', 'щ': 'o', 'з': 'p', 'х': '[', 'ъ': ']',
    'ф': 'a', 'ы': 's', 'в': 'd', 'а': 'f', 'п': 'g', 'р': 'h', 'о': 'j',
    'л': 'k', 'д': 'l', 'ж': ';', 'э': "'",
    'я': 'z', 'ч': 'x', 'с': 'c', 'м': 'v', 'и': 'b', 'т': 'n', 'ь': 'm',
    'б': ',', 'ю': '.', 'ё': '`',
    'Й': 'Q', 'Ц': 'W', 'У': 'E', 'К': 'R', 'Е': 'T', 'Н': 'Y', 'Г': 'U',
    'Ш': 'I', 'Щ': 'O', 'З': 'P', 'Х': '[', 'Ъ': ']',
    'Ф': 'A', 'Ы': 'S', 'В': 'D', 'А': 'F', 'П': 'G', 'Р': 'H', 'О': 'J',
    'Л': 'K', 'Д': 'L', 'Ж': ':', 'Э': '"',
    'Я': 'Z', 'Ч': 'X', 'С': 'C', 'М': 'V', 'И': 'B', 'Т': 'N', 'Ь': 'M',
    'Б': '<', 'Ю': '>', 'Ё': '~',
}


def truncate(text, limit=3000):
    text = (text or '').strip()
    return text if len(text) <= limit else text[:limit] + '…'


def _cyrillic_count(text):
    return sum(1 for c in text if 'а' <= c <= 'я' or 'А' <= c <= 'Я' or c in 'ёЁ')


def swap_layout(text):
    """Переключить раскладку, только если так получается больше кириллицы."""
    ru_to_en = dict(_QW)
    # В обратную сторону берём только буквы: иначе запятая превращалась
    # в «б», точка — в «ю» и обычный русский текст портился.
    en_to_ru = {v: k for k, v in _QW.items() if v.isalpha()}
    candidates = (
        text.translate(str.maketrans(ru_to_en)),
        text.translate(str.maketrans(en_to_ru)),
    )
    base = _cyrillic_count(text)
    best = max(candidates, key=_cyrillic_count)
    return best if _cyrillic_count(best) > base else text


def transcript(history, me_id, trunc=150):
    """Строки «Я: …» / «Он(а): …» из истории ai_msgs."""
    lines = []
    for m in history or ():
        who = 'Я' if m.get('sender_id') == me_id else 'Он(а)'
        text = truncate(m.get('text') or '', trunc)
        if text:
            lines.append(f'{who}: {text}')
    return '\n'.join(lines)


_DEFAULT_OPTIONS = ('Продолжить', 'Осмотреться', 'Завершить')


def parse_quest_scene(raw):
    """Разобрать ответ квеста. Возвращает (сцена, варианты).

    Модель просят вернуть строгий JSON, но она может обернуть его в
    ```json ... ``` или добавить пояснение — вырезаем объект и терпим
    частичный мусор, чтобы ход не пропадал.
    """
    text = (raw or '').strip()
    if not text:
        return 'Мастер квеста промолчал. Попробуй ещё раз.', list(_DEFAULT_OPTIONS)

    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I | re.S).strip()
    start, end = cleaned.find('{'), cleaned.rfind('}')
    payload = cleaned[start:end + 1] if start != -1 and end > start else ''
    data = None
    if payload:
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            data = None

    scene = ''
    options = []
    if isinstance(data, dict):
        scene = str(data.get('scene') or '').strip()
        raw_options = data.get('options')
        if isinstance(raw_options, list):
            for opt in raw_options:
                opt = str(opt or '').strip()
                if opt and opt not in options:
                    options.append(opt)

    if not scene:
        # Не JSON: показываем текст как сцену, даём нейтральные кнопки.
        scene = cleaned or text
        options = []
    if not options:
        options = list(_DEFAULT_OPTIONS)
    return scene, options[:4]
