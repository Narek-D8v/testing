# Тесты поиска (!search): выбор языка, очистка сниппетов, подсветка и рендер.
# Сеть не нужна — проверяются чистые функции из search.py.

import unittest
from datetime import datetime, timedelta, timezone

import search


class TestDetectLang(unittest.TestCase):
    def test_cyrillic_goes_to_russian_wiki(self):
        self.assertEqual(search.detect_lang('Пушкин'), 'ru')
        self.assertEqual(search.detect_lang('кто такой пушкин'), 'ru')

    def test_latin_goes_to_english_wiki(self):
        self.assertEqual(search.detect_lang('Pushkin'), 'en')
        self.assertEqual(search.detect_lang('python asyncio'), 'en')

    def test_mixed_query_uses_majority(self):
        # решает большинство букв: имя латиницей перевешивает короткую фамилию
        self.assertEqual(search.detect_lang('Пушкин Alexander'), 'en')
        self.assertEqual(search.detect_lang('Pushkin Александр Сергеевич'), 'ru')
        self.assertEqual(search.detect_lang('Python для начинающих'), 'ru')
        self.assertEqual(search.detect_lang('Python asyncio учебник'), 'en')

    def test_no_letters_defaults_to_english(self):
        self.assertEqual(search.detect_lang(''), 'en')
        self.assertEqual(search.detect_lang('2024'), 'en')


class TestCleanSnippet(unittest.TestCase):
    def test_strips_wikipedia_highlight_markup(self):
        raw = 'родился в <span class="searchmatch">Москве</span>'
        self.assertEqual(search.clean_snippet(raw), 'родился в Москве')

    def test_unescapes_html_entities(self):
        raw = '&quot;Евгений Онегин&quot; &amp; другие'
        self.assertEqual(search.clean_snippet(raw), '"Евгений Онегин" & другие')

    def test_removes_markdown_specials(self):
        # эти символы ломают разметку карточки
        self.assertEqual(search.clean_snippet('a*b_c`d'), 'abcd')

    def test_collapses_whitespace_and_handles_none(self):
        self.assertEqual(search.clean_snippet('  много\n\n  пробелов '), 'много пробелов')
        self.assertEqual(search.clean_snippet(None), '')

    def test_truncates_at_word_boundary(self):
        text = 'слово ' * 100
        out = search.clean_snippet(text, limit=50)
        self.assertLessEqual(len(out), 51)
        self.assertTrue(out.endswith('…'))
        self.assertNotIn('сло…', out)   # не режем слово посередине

    def test_short_text_not_truncated(self):
        self.assertEqual(search.clean_snippet('коротко', limit=50), 'коротко')


class TestHighlight(unittest.TestCase):
    def test_bolds_query_words_case_insensitive(self):
        self.assertEqual(
            search.highlight_terms('Александр Пушкин — поэт', 'пушкин'),
            'Александр **Пушкин** — поэт',
        )

    def test_ignores_short_terms(self):
        self.assertEqual(search.highlight_terms('кот и пёс', 'и'),
                         'кот и пёс')

    def test_does_not_match_inside_words(self):
        # «кот» не должен подсвечиваться внутри «который»
        self.assertEqual(search.highlight_terms('который кот', 'кот'),
                         'который **кот**')

    def test_regex_specials_in_query_are_safe(self):
        # слова режутся по не-буквам (как и в запросе к вики), спецсимвол
        # regex не ломает поиск: «$» остаётся просто символом
        self.assertEqual(search.highlight_terms('цена 100$', '100$'),
                         'цена **100**$')
        self.assertEqual(search.highlight_terms('a.b', 'a.*'), 'a.b')


class TestUrls(unittest.TestCase):
    def test_page_url_encodes_spaces(self):
        url = search.page_url('Александр Пушкин', 'ru')
        self.assertTrue(url.startswith('https://ru.wikipedia.org/wiki/'))
        self.assertIn('%D0%9F', url)      # кириллица закодирована
        self.assertNotIn(' ', url)

    def test_search_page_url(self):
        url = search.search_page_url('пушкин биография', 'ru')
        self.assertIn('w/index.php?search=', url)
        self.assertIn('%20', url)


class TestRender(unittest.TestCase):
    def _data(self, **kw):
        data = {
            'lang': 'ru',
            'totalhits': 1234,
            'suggestion': 'Александр Пушкин',
            'results': [
                {'title': 'Пушкин, Александр Сергеевич',
                 'snippet': 'русский поэт, Пушкин родился в Москве',
                 'url': 'https://ru.wikipedia.org/wiki/%D0%9F'},
                {'title': 'Евгений Онегин',
                 'snippet': 'роман в стихах, Пушкин писал его семь лет',
                 'url': 'https://ru.wikipedia.org/wiki/%D0%95'},
            ],
        }
        data.update(kw)
        return data

    def test_renders_answer_results_and_meta(self):
        card = search.render_results(
            'Пушкин', self._data(),
            {'extract': 'Пушкин — русский поэт.', 'description': 'поэт'})
        self.assertIn('🔍 Wikipedia: Пушкин', card)
        self.assertIn('📖 **Кратко**', card)
        self.assertIn('Пушкин — русский поэт.', card)
        self.assertIn('1. [Пушкин, Александр Сергеевич]', card)
        self.assertIn('— поэт', card)                 # description первого
        self.assertIn('**Пушкин**', card)             # подсветка в сниппете
        # сниппет первой статьи не дублирует выжимку
        self.assertNotIn('родился в Москве', card)
        self.assertIn('2. [Евгений Онегин]', card)
        self.assertIn('найдено ~1 234', card)
        self.assertIn('Wikipedia · RU', card)
        self.assertIn('Возможно, вы имели в виду', card)

    def test_works_without_answer_and_suggestion(self):
        card = search.render_results('Пушкин', self._data(suggestion=''),
                                     None)
        self.assertIn('1. [Пушкин, Александр Сергеевич]', card)
        self.assertNotIn('📖', card)
        self.assertNotIn('Возможно', card)

    def test_query_markdown_is_neutralized(self):
        card = search.render_results('a*b_c', self._data(totalhits=0))
        self.assertIn('🔍 Wikipedia: abc', card)
        self.assertNotIn('найдено ~', card)

    def test_empty_has_link_and_title(self):
        card = search.render_empty('абракадабра', 'ru')
        self.assertIn('ничего не найдено', card)
        self.assertIn('w/index.php?search=', card)
        self.assertIn('❌', card)


_DDG_HTML = '''
<div class="result results_links results_links_deep web-result result--ad">
  <h2 class="result__title"><a rel="nofollow" class="result__a"
     href="https://duckduckgo.com/y.js?ad_domain=shop">Купить</a></h2>
  <a class="result__snippet" href="#">Реклама</a>
</div>
<div class="result results_links results_links_deep web-result ">
  <h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpushkin&amp;rut=x">
     Пушкин — <b>статья</b></a></h2>
  <a class="result__snippet" href="#">Он <b>Пушкин</b>, &quot;поэт&quot;</a>
</div>
<div class="result results_links results_links_deep web-result ">
  <a class="result__a" href="https://example.com/pushkin">Дубль</a>
  <a class="result__snippet">дубль</a>
</div>
<div class="result results_links results_links_deep web-result ">
  <a class="result__a" href="https://ru.wikipedia.org/wiki/x">Вики</a>
  <a class="result__snippet">сниппет</a>
</div>
'''

_NEWS_RSS = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Пушкин: главное</title>
    <link>https://news.example/1</link>
    <pubDate>Mon, 18 Nov 2024 10:00:00 GMT</pubDate>
    <source url="https://ria.ru">РИА</source>
  </item>
  <item>
    <title>Без даты</title>
    <link>https://news.example/2</link>
  </item>
  <item><title>Битая — без ссылки</title></item>
</channel></rss>'''


class TestDdgParsing(unittest.TestCase):
    def test_skips_ads_duplicates_and_unwraps_links(self):
        results = search.parse_ddg_html(_DDG_HTML)
        self.assertEqual(len(results), 2)
        first = results[0]
        self.assertEqual(first['title'], 'Пушкин — статья')  # теги сняты
        self.assertEqual(first['url'], 'https://example.com/pushkin')
        self.assertEqual(first['domain'], 'example.com')
        self.assertIn('Пушкин', first['snippet'])
        self.assertEqual(results[1]['title'], 'Вики')

    def test_limit(self):
        self.assertEqual(len(search.parse_ddg_html(_DDG_HTML, limit=1)), 1)

    def test_garbage_returns_empty(self):
        self.assertEqual(search.parse_ddg_html(''), [])
        self.assertEqual(search.parse_ddg_html('<html>anomaly</html>'), [])

    def test_unwrap_url(self):
        self.assertEqual(
            search.unwrap_ddg_url(
                '//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1'),
            'https://example.com/a?b=1')
        self.assertEqual(search.unwrap_ddg_url('javascript:alert(1)'), '')


class TestNewsParsing(unittest.TestCase):
    def test_items_with_source_and_date(self):
        items = search.parse_news_rss(_NEWS_RSS)
        self.assertEqual(len(items), 2)          # без ссылки отброшена
        self.assertEqual(items[0]['title'], 'Пушкин: главное')
        self.assertEqual(items[0]['source'], 'РИА')
        self.assertIsNotNone(items[0]['published'].tzinfo)
        self.assertEqual(items[0]['published'].day, 18)
        self.assertIsNone(items[1]['published'])

    def test_source_suffix_removed_from_title(self):
        rss = ('<rss><channel><item><title>Заголовок - РИА</title>'
               '<link>https://n.example/1</link>'
               '<source>РИА</source></item></channel></rss>')
        items = search.parse_news_rss(rss)
        self.assertEqual(items[0]['title'], 'Заголовок')
        self.assertEqual(items[0]['source'], 'РИА')

    def test_broken_xml_returns_empty(self):
        self.assertEqual(search.parse_news_rss('<rss><channel>'), [])
        self.assertEqual(search.parse_news_rss(''), [])

    def test_relative_time(self):
        now = datetime(2024, 11, 20, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(search.relative_time(None), '')
        self.assertEqual(search.relative_time(now - timedelta(seconds=30), now),
                         'только что')
        self.assertEqual(search.relative_time(now - timedelta(minutes=5), now),
                         '5 мин назад')
        self.assertEqual(search.relative_time(now - timedelta(hours=3), now),
                         '3 ч назад')
        self.assertEqual(search.relative_time(now - timedelta(days=2), now),
                         '2 дн назад')
        self.assertEqual(search.relative_time(now - timedelta(days=30), now),
                         '21.10.2024')
        # будущее время не превращается в «минус N»
        self.assertEqual(search.relative_time(now + timedelta(minutes=5), now),
                         'только что')

    def test_news_page_url(self):
        url = search.news_page_url('пушкин', 'ru')
        self.assertIn('news.google.com/search?q=', url)
        self.assertIn('hl=ru', url)


class TestRenderWebNews(unittest.TestCase):
    def test_web_card(self):
        data = {'lang': 'ru', 'results': [
            {'title': 'Пушкин', 'url': 'https://example.com/p',
             'snippet': 'поэт Пушкин', 'domain': 'example.com'},
        ]}
        card = search.render_web('Пушкин', data)
        self.assertIn('🌐 Веб: Пушкин', card)
        self.assertIn('1. [Пушкин](https://example.com/p)', card)
        self.assertIn('example.com', card)
        self.assertIn('**Пушкин**', card)
        self.assertIn('DuckDuckGo · RU', card)
        self.assertIn('news', card)

    def test_web_fallback_note(self):
        data = {'lang': 'ru', 'results': [
            {'title': 'x', 'url': 'https://e.com', 'snippet': '',
             'domain': 'e.com'}]}
        self.assertIn('В Wikipedia статьи нет',
                      search.render_web('x', data, from_wiki=True))
        self.assertNotIn('В Wikipedia статьи нет', search.render_web('x', data))

    def test_link_markup_survives_brackets_and_parens(self):
        data = {'lang': 'en', 'results': [
            {'title': 'Album [deluxe]', 'url': 'https://e.com/a_(b) c',
             'snippet': '', 'domain': 'e.com'}]}
        card = search.render_web('album', data)
        self.assertIn('[Album deluxe](https://e.com/a_%28b%29%20c)', card)

    def test_web_empty(self):
        card = search.render_web('абракадабра', {'lang': 'ru', 'results': []})
        self.assertIn('ничего не найдено', card)
        self.assertIn('duckduckgo.com/?q=', card)

    def test_news_card(self):
        now = datetime.now(timezone.utc)
        data = {'lang': 'ru', 'articles': [
            {'title': 'Новость', 'url': 'https://news.example/1',
             'source': 'РИА', 'published': now - timedelta(minutes=5)},
            {'title': 'Без источника', 'url': 'https://news.example/2',
             'source': '', 'published': None},
        ]}
        card = search.render_news('Пушкин', data)
        self.assertIn('📰 Новости: Пушкин', card)
        self.assertIn('1. [Новость](https://news.example/1)', card)
        self.assertIn('РИА · 5 мин назад', card)
        self.assertIn('Google News · RU', card)

    def test_news_empty(self):
        card = search.render_news('абракадабра', {'lang': 'ru', 'articles': []})
        self.assertIn('не нашлось', card)
        self.assertIn('news.google.com/search?q=', card)


if __name__ == '__main__':
    unittest.main()
