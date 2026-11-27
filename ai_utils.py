# -*- coding: utf-8 -*-
"""Чистые помощники AI-команд: без Telethon, БД и .env (легко тестировать)."""

import html as _html
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


# ────────────────────────────────────────────────────────────
# Форматирование ответов AI для Telegram
#
# Модель любит GitHub-разметку (**жирный**, > цитата, *   список),
# которую Telegram не понимает. Ниже — конвертер в безопасный HTML
# (подмножество Bot API / Telethon) и детерминированные рендеры для
# структурированных команд. Всё на чистых функциях: тесты не тянут
# Telethon, config и БД.
# ────────────────────────────────────────────────────────────

_INLINE_RULES = (
    (re.compile(r'\*\*(.+?)\*\*', re.S), r'<b>\1</b>'),
    (re.compile(r'(?<![\w*])\*([^*\n]+)\*(?![\w*])'), r'<i>\1</i>'),
    (re.compile(r'(?<!\w)_([^_\n]+)_(?!\w)'), r'<i>\1</i>'),
    (re.compile(r'`([^`\n]+)`'), r'<code>\1</code>'),
)


def escape_html(text):
    return _html.escape('' if text is None else str(text), quote=False)


_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')


def _link_md(match):
    # Текст уже экранирован escape_html; кавычки в URL надо экранировать
    # отдельно, иначе модель сможет «закрыть» атрибут href.
    return '<a href="{}">{}</a>'.format(match.group(2).replace('"', '&quot;'),
                                         match.group(1))


def _inline_md(text):
    s = escape_html(text)
    for pattern, replacement in _INLINE_RULES:
        s = pattern.sub(replacement, s)
    s = _LINK_RE.sub(_link_md, s)
    # Незакрытые/лишние звёздочки не должны дойти до чата как мусор.
    return s.replace('**', '').replace('__', '')


def md_to_telegram_html(text):
    """Markdown ответа модели → безопасный Telegram HTML.

    Поддерживаются: **жирный**, *курсив*/_курсив_, `код`, > цитата,
    списки, заголовки. Любой «настоящий» HTML из ответа модели
    экранируется — модель не может подсунуть свой тег или атрибут.
    """
    raw = '' if text is None else str(text)
    out, quote = [], []

    def flush_quote():
        if quote:
            # Внутри blockquote обычный перевод строки сохраняется Telegram;
            # <br> Telethon просто выбрасывает как неизвестный тег.
            body = '\n'.join(_inline_md(line) for line in quote)
            out.append(f'<blockquote>{body}</blockquote>')
            quote.clear()

    for line in raw.split('\n'):
        stripped = line.strip()
        if stripped.startswith('>'):
            quoted = stripped[1:].lstrip()
            if quoted:
                quote.append(quoted)
            continue
        flush_quote()
        heading = re.match(r'^\s*#{1,6}\s+(.*)$', line)
        if heading:
            out.append(f'<b>{_inline_md(heading.group(1))}</b>')
            continue
        bullet = re.match(r'^\s*(?:[-*+]|\d+\.)\s+(.*)$', line)
        if bullet:
            out.append('• ' + _inline_md(bullet.group(1)))
            continue
        out.append(_inline_md(line))
    flush_quote()
    return '\n'.join(out).strip()


def html_to_plain(text):
    """Запасной вариант: убрать теги, если Telegram отверг HTML."""
    s = re.sub(r'<br\s*/?>|</(?:p|div|blockquote)>', '\n',
               '' if text is None else str(text), flags=re.I)
    s = re.sub(r'<[^>]+>', '', s)
    return _html.unescape(s)


def safe_truncate_html(text, limit=3800):
    """Обрезать HTML по видимым символам, закрывая открытые теги."""
    s = '' if text is None else str(text)
    if len(s) <= limit:
        return s
    out, open_tags = [], []
    i, visible = 0, 0
    while i < len(s) and visible < limit - 1:
        ch = s[i]
        if ch == '<':
            end = s.find('>', i)
            if end != -1:
                tag = s[i:end + 1]
                out.append(tag)
                name = re.match(r'<\s*([a-zA-Z0-9]+)', tag)
                if tag.startswith('</'):
                    if open_tags:
                        open_tags.pop()
                elif name and not tag.endswith('/>') and \
                        name.group(1).lower() not in ('br', 'hr', 'img'):
                    open_tags.append(name.group(1).lower())
                i = end + 1
                continue
        if ch == '&':
            end = s.find(';', i + 1)
            if end != -1 and end - i <= 12:
                out.append(s[i:end + 1])
                visible += 1
                i = end + 1
                continue
        out.append(ch)
        visible += 1
        i += 1
    out.append('…')
    out.extend(f'</{tag}>' for tag in reversed(open_tags))
    return ''.join(out)


def parse_ai_json(raw):
    """Достать JSON из ответа модели (включая ```json ... ``` и мусор вокруг)."""
    text = (raw or '').strip()
    if not text:
        return None
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I | re.S).strip()
    start, end = cleaned.find('{'), cleaned.rfind('}')
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except (ValueError, TypeError):
        return None
    # Модели иногда оборачивают объект в массив из одного элемента.
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    return data if isinstance(data, (dict, list)) else None


def _val(data, key, default='—'):
    value = data.get(key) if isinstance(data, dict) else None
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, (list, tuple)):
        return ', '.join(str(v) for v in value if v not in (None, ''))
    if isinstance(value, dict):
        return '; '.join(f'{k}: {v}' for k, v in value.items() if v not in (None, ''))
    return str(value)


def _items(data, *keys):
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


CARD_SEP = '────────────────'

_VIBE_METRICS = (
    ('🎯 Агрессия', 'aggression'),
    ('❤️ Симпатия', 'sympathy'),
    ('📊 Вовлечённость', 'engagement'),
    ('🛡️ Доверие', 'trust'),
    ('⚡ Напряжение', 'tension'),
)

_SEVERITY = {
    'высокая': '🔴 ВЫСОКАЯ', 'high': '🔴 ВЫСОКАЯ',
    'средняя': '🟠 СРЕДНЯЯ', 'medium': '🟠 СРЕДНЯЯ', 'moderate': '🟠 СРЕДНЯЯ',
    'низкая': '🟢 НИЗКАЯ', 'low': '🟢 НИЗКАЯ',
}


def _get(data, key):
    """Значение поля или None (в отличие от _val не подставляет прочерк)."""
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return value


def _short(value, limit=180):
    """Одна строка без переводов: модель часто присылает многострочные цитаты."""
    text = ' '.join(str(value or '').split())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + '…'


def _quote(value, limit=180):
    text = _short(value, limit).strip('«»"\' ')
    return f'«{text}»' if text else ''


def score(value, default=0):
    """Привести '85%', 0.85, 85 или 'высокий' к числу 0..100."""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        number = float(value)
        if 0 < number <= 1:
            number *= 100
    else:
        text = str(value).strip().lower().replace(',', '.')
        match = re.search(r'\d+(?:\.\d+)?', text)
        if match:
            number = float(match.group())
            if '%' not in text and 0 < number <= 1:
                number *= 100
        else:
            for word, points in (
                ('крайн', 95), ('сильн', 85), ('высок', 80),
                ('умерен', 55), ('средн', 55), ('слаб', 25), ('низк', 15),
            ):
                if word in text:
                    number = points
                    break
            else:
                return default
    return max(0, min(100, int(round(number))))


def meter(value, width=10):
    """Визуальная шкала: ██████░░░░ (в <code> gets monospace)."""
    points = score(value)
    # Округление вверх: 5% должны быть видны как один заполненный блок,
    # а не как пустая шкала.
    filled = min(width, (points * width + 99) // 100)
    return '█' * filled + '░' * (width - filled)


def timestamp_label(ts):
    """`date` из ai_msgs — epoch seconds; аккуратно обрабатываем и datetime."""
    if not ts:
        return ''
    try:
        if hasattr(ts, 'strftime'):
            return ts.strftime('%d.%m %H:%M')
        from datetime import datetime
        return datetime.fromtimestamp(float(ts)).strftime('%d.%m %H:%M')
    except (ValueError, TypeError, OSError):
        return ''


def transcript_dated(history, me_id, trunc=160, limit=None):
    """Транскрипт с датами — важен для хронологии противоречий."""
    rows = list(history or ())
    if limit:
        rows = rows[-limit:]
    lines = []
    for m in rows:
        text = truncate((m.get('text') or '').strip(), trunc)
        if not text:
            continue
        who = 'Я' if m.get('sender_id') == me_id else 'Он(а)'
        stamp = timestamp_label(m.get('date'))
        prefix = f'{stamp} ' if stamp else ''
        lines.append(f'[{prefix}{who}] {text}')
    return '\n'.join(lines)


def render_vibe(data):
    """Богатый ответ !vibe: шкалы, доказательства, динамика, триггеры, совет."""
    if not isinstance(data, dict):
        return ''
    scores = data.get('scores') if isinstance(data.get('scores'), dict) else data
    lines = []

    headline = _get(data, 'summary') or _get(data, 'tone')
    if headline:
        lines.append(f'<b>Коротко:</b> {md_to_telegram_html(_short(headline, 240))}')

    shown = 0
    for label, key in _VIBE_METRICS:
        value = _get(scores, key)
        if value is None:
            continue
        points = score(value)
        lines.append(f'{label} — <code>{meter(points)}</code> <b>{points}%</b>')
        shown += 1
    if not shown and lines:
        lines.append('<i>Модель не дала числовых оценок — только качественный разбор.</i>')

    evidence = _items(data, 'evidence', 'signals', 'examples')
    if evidence:
        lines.append('')
        lines.append('<b>🔎 На чём основано</b>')
        for number, item in enumerate(evidence[:3], 1):
            if not isinstance(item, dict):
                lines.append(f'• {md_to_telegram_html(_short(item))}')
                continue
            signal = _short(_get(item, 'signal') or _get(item, 'sign') or '', 100)
            quote = _quote(_get(item, 'quote') or '', 170)
            meaning = _short(_get(item, 'meaning') or '', 180)
            header = f'{number}. {escape_html(signal)}' if signal else f'{number}.'
            lines.append(f'<b>{header}</b>')
            if quote:
                lines.append(f'<blockquote>{escape_html(quote)}</blockquote>')
            if meaning:
                lines.append(f'↳ {md_to_telegram_html(meaning)}')

    dynamics = _items(data, 'dynamics', 'timeline')
    if dynamics:
        lines.append('')
        lines.append('<b>📈 Динамика</b>')
        for item in dynamics[:3]:
            if not isinstance(item, dict):
                lines.append('• ' + md_to_telegram_html(_short(item, 180)))
                continue
            stage = _short(_get(item, 'stage') or _get(item, 'phase') or '', 50)
            mood = _short(_get(item, 'mood') or _get(item, 'state') or '', 150)
            why = _short(_get(item, 'why') or _get(item, 'reason') or '', 120)
            prefix = f'<b>{escape_html(stage)}:</b> ' if stage else ''
            line = prefix + md_to_telegram_html(mood)
            if why:
                line += f' <i>({md_to_telegram_html(why)})</i>'
            lines.append('• ' + line)
    else:
        dynamic = _get(data, 'dynamic')
        if dynamic:
            lines.append('')
            lines.append(f'<b>📈 Динамика:</b> {md_to_telegram_html(_short(dynamic, 240))}')

    for key, aliases, icon, title in (
        ('triggers', ('trigger',), '⚡', 'Что усиливает эмоции'),
        ('red_flags', ('flags', 'concerns'), '🚩', 'Тревожные сигналы'),
    ):
        values = _items(data, key, *aliases)
        if not values:
            continue
        lines.append('')
        lines.append(f'<b>{icon} {title}</b>')
        for value in values[:4]:
            lines.append('• ' + md_to_telegram_html(_short(value, 160)))

    advice = _get(data, 'advice') or _get(data, 'recommendation')
    if advice:
        lines.append('')
        lines.append('<b>💡 Что делать</b>')
        lines.append(md_to_telegram_html(_short(advice, 400)))
    return '\n'.join(lines).strip()


def render_liar(data):
    """Богатый ответ !liar: вердикт, шкала, цитаты с контекстом, вопросы."""
    if not isinstance(data, dict):
        return ''
    lines = []
    contradictions = _items(data, 'contradictions', 'items', 'points')
    verdict = _short(_get(data, 'verdict') or '', 200)
    summary = _short(_get(data, 'summary') or '', 300)
    credibility = _get(data, 'credibility')

    if credibility is not None:
        points = score(credibility)
        lines.append(f'<b>Достоверность:</b> <code>{meter(points)}</code> <b>{points}%</b>')
    if verdict:
        lines.append(f'<b>Вердикт:</b> {md_to_telegram_html(verdict)}')
    if not contradictions and not verdict:
        lines.append('<b>✅ Явных противоречий не найдено</b>')
        if summary and re.search(r'не найдено|нет противореч', summary, re.I):
            summary = ''
    if summary:
        lines.append(md_to_telegram_html(summary))

    for number, item in enumerate(contradictions[:5], 1):
        if not isinstance(item, dict):
            lines.append('')
            lines.append(f'<b>{number}.</b> {md_to_telegram_html(_short(item))}')
            continue
        claim = _short(_get(item, 'claim') or _get(item, 'claim_a') or
                       f'Противоречие {number}', 160)
        severity = _SEVERITY.get(_short(_get(item, 'severity') or '', 30).lower(), '')
        header = f'<b>{number}. {escape_html(claim)}</b>'
        if severity:
            header += f'  <code>{severity}</code>'
        lines.append('')
        lines.append(header)
        for field, context_field in (('quote_a', 'context_a'), ('quote_b', 'context_b')):
            quote = _quote(_get(item, field) or '', 190)
            context = _short(_get(item, context_field) or '', 110)
            if quote:
                lines.append(f'<blockquote>{escape_html(quote)}</blockquote>')
            if context:
                lines.append(f'↳ {escape_html(context)}')
        why = _short(_get(item, 'why') or _get(item, 'note') or '', 220)
        if why:
            lines.append(f'<i>Почему:</i> {md_to_telegram_html(why)}')

    for key, aliases, icon, title in (
        ('unverified', ('unconfirmed', 'gaps'), '⚠️', 'Без подтверждения'),
        ('questions', ('ask', 'followup'), '❓', 'Что спросить'),
    ):
        values = _items(data, key, *aliases)
        if not values:
            continue
        lines.append('')
        lines.append(f'<b>{icon} {title}</b>')
        for value in values[:4]:
            lines.append('• ' + md_to_telegram_html(_short(value, 160)))

    advice = _get(data, 'advice') or _get(data, 'recommendation')
    if advice:
        lines.append('')
        lines.append('<b>💡 Как действовать</b>')
        lines.append(md_to_telegram_html(_short(advice, 400)))
    return '\n'.join(lines).strip()


def render_summary(data):
    """Структурированный ответ !summary: ровно 4 поля."""
    if not isinstance(data, dict):
        return ''
    lines = []
    for key, label in (
        ('who', 'Кто'),
        ('about', 'О чём'),
        ('direction', 'Куда движется'),
        ('conclusion', 'Вывод'),
    ):
        value = _val(data, key, '')
        if value and value != '—':
            lines.append(f'• <b>{label}:</b> {md_to_telegram_html(value)}')
    return '\n'.join(lines)


def confidence(value, evidence=None, samples=None):
    """Уверенность 0..100 с локальными «шляпками» против самоуверенности модели."""
    points = score(value, default=0)
    if evidence is not None and not evidence:
        points = min(points, 40)
    if samples is not None and samples < 10:
        points = min(points, 50)
    return points


def render_promises(items, summary='', show_status=False):
    """Список обещаний из БД (открытые — источник истины, не память модели)."""
    lines = []
    if summary:
        lines.append(md_to_telegram_html(_short(summary, 300)))
    if not items:
        lines.append('<b>✅ Открытых обещаний нет</b>')
        return '\n'.join(lines).strip()
    lines.append(f'<b>⏳ Открытых: {len(items)}</b>')
    for number, item in enumerate(items, 1):
        text = _short(_get(item, 'text') or '', 220)
        who = _short(_get(item, 'who_label') or '', 60)
        due = _short(_get(item, 'due_at') or '', 60)
        note = _short(_get(item, 'note') or '', 180)
        status = _short(_get(item, 'status') or '', 20)
        head = f'<b>{number}. {escape_html(text)}</b>'
        if show_status and status:
            head += f'  <code>{escape_html(status.upper())}</code>'
        lines.append('')
        lines.append(head)
        meta = []
        if who:
            meta.append(f'👤 {escape_html(who)}')
        if due:
            meta.append(f'📅 {escape_html(due)}')
        if meta:
            lines.append(' · '.join(meta))
        if note:
            lines.append(f'↳ {escape_html(note)}')
        if show_status:
            pid = _get(item, 'id')
            if pid is not None:
                lines.append(f'<i>#{escape_html(pid)}</i>')
    return '\n'.join(lines).strip()


def render_mediation(data):
    """План примирения: почва → шаги с фразами → границы → вопросы."""
    if not isinstance(data, dict):
        return ''
    lines = []
    situation = _short(_get(data, 'diagnosis') or '', 300)
    if situation:
        lines.append(f'<b>🧭 Ситуация:</b> {md_to_telegram_html(situation)}')

    ground = _items(data, 'common_ground', 'common')
    if ground:
        lines.append('')
        lines.append('<b>🤝 Общая почва</b>')
        for value in ground[:4]:
            lines.append('• ' + md_to_telegram_html(_short(value, 200)))

    steps = _items(data, 'steps', 'phrases')
    if steps:
        lines.append('')
        lines.append('<b>🗣 Что сказать</b>')
        for number, item in enumerate(steps[:5], 1):
            if not isinstance(item, dict):
                lines.append(f'<b>{number}.</b> <code>{escape_html(_short(item, 260))}</code>')
                continue
            say = _short(_get(item, 'say') or _get(item, 'text') or '', 260)
            why = _short(_get(item, 'why') or '', 180)
            lines.append(f'<b>{number}.</b> <code>{escape_html(say)}</code>' if say
                         else f'<b>{number}.</b>')
            if why:
                lines.append(f'   <i>{md_to_telegram_html(why)}</i>')

    for key, aliases, icon, title in (
        ('boundaries', ('limits',), '🚧', 'Границы'),
        ('questions', ('ask',), '❓', 'Вопросы'),
    ):
        values = _items(data, key, *aliases)
        if values:
            lines.append('')
            lines.append(f'<b>{icon} {title}</b>')
            for value in values[:4]:
                lines.append('• ' + md_to_telegram_html(_short(value, 200)))

    advice = _get(data, 'advice') or _get(data, 'conclusion')
    if advice:
        lines.append('')
        lines.append('<b>💡 Итог</b>')
        lines.append(md_to_telegram_html(_short(advice, 400)))
    lines.append('')
    lines.append('<i>Формулировки — не сценарий для давления, а повод поговорить.</i>')
    return '\n'.join(lines).strip()


def render_reality(data):
    """Разделяет доказанные факты и интерпретации — без обвинений."""
    if not isinstance(data, dict):
        return ''
    lines = []
    facts = _items(data, 'facts', 'events')
    if facts:
        lines.append('<b>📌 Что реально было</b>')
        for item in facts[:5]:
            if not isinstance(item, dict):
                lines.append('• ' + md_to_telegram_html(_short(item, 220)))
                continue
            quote = _quote(_get(item, 'quote') or '', 190)
            when = _short(_get(item, 'when') or '', 60)
            what = _short(_get(item, 'what') or '', 200)
            if quote:
                lines.append(f'<blockquote>{escape_html(quote)}</blockquote>')
            prefix = f'🕒 {escape_html(when)} — ' if when else ''
            if what:
                lines.append('• ' + prefix + md_to_telegram_html(what))

    views = _items(data, 'interpretations', 'thoughts')
    if views:
        lines.append('')
        lines.append('<b>🧠 Как это трактуется</b>')
        for item in views[:4]:
            if not isinstance(item, dict):
                lines.append('• ' + md_to_telegram_html(_short(item, 220)))
                continue
            claim = _short(_get(item, 'claim') or '', 160)
            basis = _short(_get(item, 'basis') or '', 180)
            alternative = _short(_get(item, 'alternative') or '', 200)
            lines.append(f'<b>• {escape_html(claim)}</b>' if claim else '<b>•</b>')
            if basis:
                lines.append(f'   ↳ на чём основано: {md_to_telegram_html(basis)}')
            if alternative:
                lines.append(f'   ↳ другое объяснение: {md_to_telegram_html(alternative)}')

    verdict = _short(_get(data, 'verdict') or '', 300)
    if verdict:
        lines.append('')
        lines.append(f'<b>⚖️ Итог:</b> {md_to_telegram_html(verdict)}')
    advice = _short(_get(data, 'advice') or '', 300)
    if advice:
        lines.append(f'<b>💡 Как не нагнетать:</b> {md_to_telegram_html(advice)}')
    return '\n'.join(lines).strip()


def render_roles(data, samples=None):
    """Роли участников с уверенностью; это гипотезы, а не диагноз."""
    if not isinstance(data, dict):
        return ''
    lines = []
    for item in _items(data, 'participants', 'users')[:6]:
        if not isinstance(item, dict):
            continue
        user = _short(_get(item, 'user') or _get(item, 'name') or 'Участник', 80)
        lines.append(f'<b>👤 {escape_html(user)}</b>')
        summary = _short(_get(item, 'summary') or '', 200)
        if summary:
            lines.append(md_to_telegram_html(summary))
        for role in _items(item, 'roles', 'types')[:4]:
            if not isinstance(role, dict):
                lines.append('• ' + md_to_telegram_html(_short(role, 160)))
                continue
            rtype = _short(_get(role, 'type') or '', 80) or 'роль'
            evidence = [str(v) for v in _items(role, 'evidence', 'quotes') if v]
            points = confidence(_get(role, 'confidence'), evidence, samples)
            lines.append(f'🎭 {escape_html(rtype)} — <code>{meter(points)}</code> '
                         f'<b>{points}%</b>')
            for quote in evidence[:2]:
                lines.append(f'<blockquote>{escape_html(_quote(quote, 160))}</blockquote>')
            why = _short(_get(role, 'why') or '', 180)
            if why:
                lines.append(f'<i>↳ {md_to_telegram_html(why)}</i>')
        lines.append('')
    dynamic = _short(_get(data, 'dynamic') or '', 300)
    if dynamic:
        lines.append(f'<b>🔄 Динамика:</b> {md_to_telegram_html(dynamic)}')
    caveats = _short(_get(data, 'caveats') or '', 300)
    if caveats:
        lines.append(f'<b>⚠️ Оговорки:</b> {md_to_telegram_html(caveats)}')
    lines.append('')
    lines.append('<i>Оценка по переписке — поведенческие гипотезы, не диагноз.</i>')
    return '\n'.join(lines).strip()


def compose_card(title, body, footer='', meta=''):
    """Собрать HTML-карточку: заголовок → разделитель → тело → мета → провайдер."""
    parts = [f'<b>{escape_html(title)}</b>', CARD_SEP]
    body = (body or '').strip()
    if body:
        parts.append(body)
    tail = []
    if meta:
        tail.append(escape_html(meta))
    if footer:
        tail.append(escape_html(f'[{footer}]'))
    if tail:
        parts.extend([CARD_SEP, ' · '.join(tail)])
    return '\n\n'.join(parts)
