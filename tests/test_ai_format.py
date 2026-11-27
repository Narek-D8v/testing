# Форматирование ответов AI: модель часто отдаёт GitHub-разметку, которую
# Telegram не рендерит. Проверяем конвертер и структурированные рендеры без
# импорта config/БД (Telethon — опционально).

import unittest

from ai_utils import (
    compose_card, confidence, html_to_plain, md_to_telegram_html, parse_ai_json,
    render_liar, render_mediation, render_promises, render_reality, render_roles,
    render_summary, render_vibe, safe_truncate_html, transcript_dated,
)

try:
    from telethon.extensions import html as tele_html
except ImportError:
    tele_html = None


RAW_LIAR = """В предоставленном диалоге зафиксировано прямое противоречие.

**Цитатная пара:**

*   **Версия №1 (телефон возвращен):**
    > **Он(а):** «Нуууу / Я сама взяла»
    > **Он(а):** «Подошла и из рук взяла»

*   **Версия №2 (телефон у матери):**
    > **Он(а):** «Она сказала "если не отдашь телефон не получишь"»

*(Примечание: также есть внутреннее противоречие).*

[Gemini]
"""


VIBE_RICH = {
    'summary': 'Спокойный, тёплый тон с лёгким напряжением в конце.',
    'scores': {'aggression': 5, 'sympathy': 95, 'engagement': 90,
               'trust': 80, 'tension': 'низкая'},
    'evidence': [
        {'signal': 'Поддерживает вас', 'quote': 'Конечно, я рядом',
         'meaning': 'высокая симпатия'},
        {'signal': 'Резкий тон ближе к концу', 'quote': 'Ну хватит уже',
         'meaning': 'небольшое напряжение'},
    ],
    'dynamics': [
        {'stage': 'Начало', 'mood': 'дружелюбно', 'why': 'лёгкий Small talk'},
        {'stage': 'Конец', 'mood': 'сдержанно', 'why': 'спор о телефоне'},
    ],
    'triggers': [' UPPERCASE-аргументы', 'долгое молчание'],
    'red_flags': [],
    'advice': 'Сначала признайте её позицию, потом обсуждайте телефон.',
}

LIAR_RICH = {
    'verdict': 'Слова часто не совпадают с фактами',
    'credibility': 35,
    'summary': 'Главное расхождение — судьба телефона.',
    'contradictions': [{
        'claim': 'Телефон якобы уже возвращён',
        'severity': 'высокая',
        'quote_a': 'Я сама взяла телефон со стола',
        'context_a': '01.01 12:00',
        'quote_b': 'Телефон всё ещё у матери',
        'context_b': '03.01 19:30',
        'why': 'Нельзя одновременно вернуть и оставить у матери',
        'questions': ['Когда именно он был возвращён?'],
    }],
    'unverified': ['Утверждение про «мать» ни разу не подтверждено'],
    'questions': ['Попросите показать переписку с матерью'],
    'advice': 'Уточните даты до выводов.',
}


class TestMarkdownConverter(unittest.TestCase):
    def test_github_markdown_is_converted(self):
        html = md_to_telegram_html(RAW_LIAR)
        self.assertIn('<b>Цитатная пара:</b>', html)
        self.assertIn('• <b>Версия №1', html)
        self.assertIn('<blockquote>', html)
        self.assertIn('<b>Он(а):</b>', html)
        self.assertNotIn('**', html)
        self.assertNotIn('*   ', html)
        self.assertNotIn('\n> ', html)

    def test_raw_html_from_model_is_escaped(self):
        html = md_to_telegram_html('<script>alert(1)</script> **жирный**')
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertIn('<b>жирный</b>', html)

    def test_quotes_are_grouped(self):
        html = md_to_telegram_html('> строка 1\n> строка 2\n\nобычный текст')
        self.assertEqual(html.count('<blockquote>'), 1)
        self.assertIn('строка 1\nстрока 2', html)
        self.assertIn('обычный текст', html)


class TestStructuredRenderers(unittest.TestCase):
    def test_vibe_json(self):
        data = parse_ai_json('''```json
        {"aggression": "5%", "sympathy": "95%", "engagement": "90%",
         "tone": "нежное общение", "advice": "завершать конфликты вовремя"}
        ```''')
        self.assertIsNotNone(data)
        html = render_vibe(data)
        self.assertIn('🎯 Агрессия — <code>█░░░░░░░░░</code> <b>5%</b>', html)
        self.assertIn('❤️ Симпатия — <code>██████████</code> <b>95%</b>', html)
        self.assertIn('<b>Коротко:</b> нежное общение', html)
        self.assertIn('<b>💡 Что делать</b>', html)
        self.assertNotIn('**', html)

    def test_vibe_rich_sections(self):
        html = render_vibe(VIBE_RICH)
        self.assertIn('<b>🔎 На чём основано</b>', html)
        self.assertIn('<blockquote>«Конечно, я рядом»</blockquote>', html)
        self.assertIn('↳ высокая симпатия', html)
        self.assertIn('<b>📈 Динамика</b>', html)
        self.assertIn('<b>Начало:</b> дружелюбно', html)
        self.assertIn('<b>⚡ Что усиливает эмоции</b>', html)
        self.assertIn('<b>💡 Что делать</b>', html)
        self.assertIn('⚡ Напряжение — <code>██░░░░░░░░</code> <b>15%</b>', html)
        self.assertNotIn('**', html)

    def test_vibe_plain_text_fallback(self):
        # Модель не прислала JSON — рендер не должен показывать сырые звёзды.
        self.assertIsNone(parse_ai_json('🎯 Агрессия: **5%**'))
        html = render_vibe({'aggression': '**5%**'})
        self.assertIn('<b>5%</b>', html)
        self.assertNotIn('**', html)

    def test_liar_quotes(self):
        data = parse_ai_json('''{"summary": "Есть противоречие", "contradictions": [
            {"claim": "Телефон возвращён", "quote_a": "Я сама взяла",
             "quote_b": "Телефон у матери", "note": "ключевое расхождение"}]}''')
        html = render_liar(data)
        self.assertIn('<b>1. Телефон возвращён</b>', html)
        self.assertEqual(html.count('<blockquote>'), 2)
        self.assertIn('<i>Почему:</i> ключевое расхождение', html)

    def test_liar_rich_sections(self):
        html = render_liar(LIAR_RICH)
        self.assertIn('<b>Достоверность:</b> <code>', html)
        self.assertIn('<b>35%</b>', html)
        self.assertIn('<b>Вердикт:</b> Слова часто не совпадают с фактами', html)
        self.assertIn('<code>🔴 ВЫСОКАЯ</code>', html)
        self.assertIn('<blockquote>«Я сама взяла телефон со стола»</blockquote>', html)
        self.assertIn('↳ 01.01 12:00', html)
        self.assertIn('<b>⚠️ Без подтверждения</b>', html)
        self.assertIn('<b>❓ Что спросить</b>', html)
        self.assertIn('<b>💡 Как действовать</b>', html)
        self.assertNotIn('**', html)

    def test_liar_escapes_model_html(self):
        html = render_liar({'contradictions': [{
            'claim': '<b>враньё</b>',
            'quote_a': '<script>alert(1)</script>',
        }]})
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&lt;b&gt;враньё&lt;/b&gt;', html)

    def test_liar_no_contradictions(self):
        html = render_liar({'summary': 'Противоречий не найдено', 'contradictions': []})
        self.assertEqual(html, '<b>✅ Явных противоречий не найдено</b>')

    def test_summary_fields(self):
        html = render_summary({'who': 'Жена', 'about': 'о телефоне',
                               'direction': 'к примирению', 'conclusion': 'всё хорошо'})
        self.assertIn('• <b>Кто:</b> Жена', html)
        self.assertIn('• <b>Вывод:</b> всё хорошо', html)

    def test_parse_ai_json_unwraps_array(self):
        self.assertEqual(parse_ai_json('[{"aggression": 5}]'), {'aggression': 5})

    def test_transcript_dated(self):
        rows = [
            {'sender_id': 1, 'text': 'привет', 'date': 1767225600},  # 01.01.2026 00:00 UTC
            {'sender_id': 2, 'text': 'здравствуй', 'date': 1767225660},
        ]
        out = transcript_dated(rows, 1)
        self.assertRegex(out, r'\[\d{2}\.\d{2} \d{2}:\d{2} Я\] привет')
        self.assertRegex(out, r'\[\d{2}\.\d{2} \d{2}:\d{2} Он\(а\)\] здравствуй')

    def test_card_escapes_and_meta(self):
        html = compose_card('🎭 Настроение · <Жена>', 'тело', 'Gemini & Co',
                            meta='20 сообщений')
        self.assertIn('&lt;Жена&gt;', html)
        self.assertIn('[Gemini &amp; Co]', html)
        self.assertIn('20 сообщений', html)
        self.assertNotIn('<Жена>', html)
        self.assertEqual(html.count('────────────────'), 2)


class TestNewCommands(unittest.TestCase):
    def test_promises_render_from_db_rows(self):
        rows = [
            {'id': 7, 'text': 'верну телефон', 'who_label': 'Жена',
             'due_at': '03.01', 'note': 'в поиске', 'status': 'open'},
        ]
        html = render_promises(rows, summary='Пока ждём')
        self.assertIn('<b>⏳ Открытых: 1</b>', html)
        self.assertIn('<b>1. верну телефон</b>', html)
        self.assertIn('👤 Жена', html)
        self.assertIn('📅 03.01', html)
        self.assertNotIn('**', html)

    def test_promises_empty_and_archive(self):
        self.assertEqual(render_promises([]), '<b>✅ Открытых обещаний нет</b>')
        archived = render_promises(
            [{'id': 1, 'text': 'купил', 'status': 'done'}], show_status=True)
        self.assertIn('<code>DONE</code>', archived)
        self.assertIn('#1', archived)

    def test_mediation_render(self):
        data = parse_ai_json('''{"diagnosis": "спор о телефоне",
          "common_ground": ["важно доверие"],
          "steps": [{"say": "Я понимаю тебя", "why": "снижает оборону"}],
          "boundaries": ["не обещать невозможного"],
          "questions": ["что для тебя важно?"], "advice": "говорить спокойнее"}''')
        html = render_mediation(data)
        self.assertIn('<b>🧭 Ситуация:</b> спор о телефоне', html)
        self.assertIn('<b>🤝 Общая почва</b>', html)
        self.assertIn('<code>Я понимаю тебя</code>', html)
        self.assertIn('<b>🚧 Границы</b>', html)
        self.assertIn('<b>❓ Вопросы</b>', html)

    def test_reality_render(self):
        data = parse_ai_json('''{"facts": [{"quote": "я вернула", "when": "01.01",
          "what": "телефон был возвращён"}],
          "interpretations": [{"claim": "она обманула", "basis": "другая дата",
          "alternative": "перепутала даты"}],
          "verdict": "факт подтверждён", "advice": "уточнить дату"}''')
        html = render_reality(data)
        self.assertIn('<blockquote>«я вернула»</blockquote>', html)
        self.assertIn('🕒 01.01', html)
        self.assertIn('другое объяснение: перепутала даты', html)
        self.assertIn('<b>⚖️ Итог:</b>', html)

    def test_roles_confidence_caps_and_disclaimer(self):
        self.assertEqual(confidence(95, evidence=[], samples=100), 40)
        self.assertEqual(confidence(95, evidence=['цитата'], samples=3), 50)
        self.assertEqual(confidence(95, evidence=['цитата'], samples=100), 95)
        data = parse_ai_json('''{"participants": [
          {"user": "Жена", "summary": "задаёт тон", "roles": [
            {"type": "манипулятор (по поведению)", "confidence": 95,
             "evidence": ["<script>алерт</script>"], "why": "давит на вину"}]}],
          "dynamic": "роли меняются", "caveats": "мало данных"}''')
        html = render_roles(data, samples=100)
        self.assertIn('👤 Жена', html)
        self.assertIn('<b>95%</b>', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('не диагноз', html)

    def test_roles_low_confidence_without_evidence(self):
        html = render_roles({'participants': [
            {'user': 'Жена', 'roles': [
                {'type': 'избегающий', 'confidence': 88, 'evidence': []}]}
        ]}, samples=50)
        self.assertIn('<b>40%</b>', html)


class TestTelegramEntities(unittest.TestCase):
    @unittest.skipIf(tele_html is None, 'telethon не установлен')
    def test_renderers_produce_real_entities(self):
        from ai_utils import compose_card
        for renderer, payload in ((render_vibe, VIBE_RICH), (render_liar, LIAR_RICH)):
            card = compose_card('Тест', renderer(payload), 'Gemini', meta='20 сообщений')
            clean, entities = tele_html.parse(card)
            types = {type(entity).__name__ for entity in entities}
            self.assertIn('MessageEntityBold', types)
            self.assertIn('MessageEntityCode', types)
            self.assertIn('MessageEntityBlockquote', types)
            self.assertNotIn('**', clean)
            self.assertNotIn('>', clean)


class TestSafeTruncate(unittest.TestCase):
    def test_keeps_tags_closed(self):
        text = '<b>hello</b> world <i>there is more text</i>'
        cut = safe_truncate_html(text, 20)
        self.assertNotIn('**', cut)
        self.assertEqual(cut.count('<b>'), cut.count('</b>'))
        self.assertEqual(cut.count('<i>'), cut.count('</i>'))
        self.assertTrue(cut.endswith('…</i>'))

    def test_short_text_unchanged(self):
        self.assertEqual(safe_truncate_html('<b>ok</b>', 100), '<b>ok</b>')

    def test_html_to_plain_fallback(self):
        plain = html_to_plain('<b>Привет</b><br>мир &amp; дом')
        self.assertIn('Привет\nмир & дом', plain)
        self.assertNotIn('<b>', plain)


if __name__ == '__main__':
    unittest.main()
