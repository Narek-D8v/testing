# search.py — поиск в Wikipedia без внешних ключей и зависимостей.
#
# Что здесь, а не в хендлере:
#   * выбор языка по запросу (кириллица → ru, иначе en): раньше любой
#     запрос переводился на английский, из-за чего русские статьи искались
#     по машинному переводу и находились хуже;
#   * очистка сниппетов: Wikipedia отдаёт HTML (<span class="searchmatch">,
#     сущности), а карточка рендерится в markdown — теги и «*» ломали вывод;
#   * краткая выжимка по первому результату через REST summary;
#   * подсказка «возможно, вы имели в виду» из searchinfo.suggestion;
#   * веб-поиск DuckDuckGo и новости Google News для запросов, которых нет
#     в вики (см. `!search web` / `!search news`).
# Всё, что не требует сети (язык, сниппеты, рендер), вынесено в чистые
# функции и покрыто тестами в tests/test_search.py.

import email.utils
import html
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlsplit
from xml.etree import ElementTree

import aiohttp

import ui

_USER_AGENT = (
    'TelegramUserBot/1.0 (Wikipedia search; '
    '+https://meta.wikimedia.org/wiki/User-Agent_policy)'
)
_API = 'https://{lang}.wikipedia.org/w/api.php'
_SUMMARY = 'https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}'
_PAGE = 'https://{lang}.wikipedia.org/wiki/{title}'
_SEARCH_PAGE = 'https://{lang}.wikipedia.org/w/index.php?search={query}'
_WEB = 'https://html.duckduckgo.com/html/'
_NEWS = 'https://news.google.com/rss/search'
_WEB_PAGE = 'https://duckduckgo.com/?q={query}'

# DuckDuckGo и Google отдают HTML/RSS только браузерным клиентам:
# на «вежливый» бот-UA первый отвечает страницей-заглушкой.
_BROWSER_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'
)

_TIMEOUT = 15
_SNIPPET_LIMIT = 230
_ANSWER_LIMIT = 420
_MD_SPECIALS = re.compile(r'[*_`]')
_CYRILLIC = re.compile(r'[\u0400-\u04FF]')
_LATIN = re.compile(r'[A-Za-z]')


def detect_lang(text):
    """Язык вики по самому запросу: кириллица → ru, всё остальное → en.

    Смешанный запрос решает по большинству букв, запрос без букв — en
    (цифры/символы одинаково ищутся в любом разделе)."""
    text = text or ''
    cyr = len(_CYRILLIC.findall(text))
    lat = len(_LATIN.findall(text))
    return 'ru' if cyr and cyr >= lat else 'en'


def clean_query(text):
    """Запрос, безопасный для заголовка карточки (markdown ломают * _ `)."""
    text = _MD_SPECIALS.sub('', str(text or ''))
    return re.sub(r'\s+', ' ', text).strip()


def truncate_words(text, limit):
    """Обрезка по границе слова с многоточием, а не по середине слова."""
    text = text or ''
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if ' ' in cut:
        cut = cut.rsplit(' ', 1)[0]
    return cut.rstrip(' ,.;:—-') + '…'


def _plain(raw):
    """HTML-фрагмент → одна строка текста без тегов и markdown-символов."""
    text = re.sub(r'<[^>]+>', '', raw or '')
    text = html.unescape(text).replace('\u200b', ' ')
    text = _MD_SPECIALS.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()


def clean_snippet(raw, limit=_SNIPPET_LIMIT):
    """HTML-сниппет → плоский текст для карточки.

    Убираем теги (в т.ч. <span class="searchmatch">), раскрываем сущности
    (&quot;, &amp;), выкидываем markdown-символы и лишние пробелы."""
    return truncate_words(_plain(raw), limit)


def highlight_terms(text, query):
    """Подсветка слов запроса в сниппете: **слово**, без учёта регистра.

    Короткие слова (<3 символов) не трогаем — иначе «и», «в» подсвечивают
    половину текста. Слова берём по границам, поэтому подстроки не мажут."""
    terms = {t for t in re.split(r'\W+', query or '', flags=re.UNICODE)
             if len(t) >= 3}
    out = text or ''
    for term in sorted(terms, key=len, reverse=True):
        pattern = r'(?<!\w)(' + re.escape(term) + r')(?!\w)'
        out = re.sub(pattern, r'**\1**', out, count=1, flags=re.IGNORECASE)
    return out


def _link_title(text):
    """Текст ссылки без символов, ломающих markdown-разметку Telegram."""
    return clean_query(text).replace('[', '').replace(']', '')


def _link_url(url):
    """URL без пробелов и круглых скобок: «)` закрывает ссылку в markdown."""
    return (url or '').replace(' ', '%20').replace('(', '%28').replace(')', '%29')


def page_url(title, lang):
    return _PAGE.format(lang=lang, title=quote((title or '').replace(' ', '_')))


def search_page_url(query, lang):
    return _SEARCH_PAGE.format(lang=lang, query=quote(query or ''))


@asynccontextmanager
async def _request(url, params=None, data=None, timeout=_TIMEOUT, headers=None):
    """GET/POST с проверкой статуса. Возвращает объект ответа внутри сессии.

    Не-200 — исключение: вызывающий решает, это ошибка (карточка с текстом)
    или повод попробовать другой источник."""
    hdrs = {'User-Agent': _BROWSER_UA, 'Accept-Language': 'ru,en;q=0.8'}
    if headers:
        hdrs.update(headers)
    async with aiohttp.ClientSession(
        headers=hdrs,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as session:
        method = session.post if data is not None else session.get
        async with method(url, params=params, data=data) as resp:
            if resp.status != 200:
                raise ConnectionError(f'HTTP {resp.status}')
            yield resp


async def _get_json(url, params=None, timeout=_TIMEOUT):
    async with _request(url, params=params, timeout=timeout,
                        headers={'User-Agent': _USER_AGENT}) as resp:
        return await resp.json(content_type=None)


async def _get_text(url, params=None, data=None, timeout=_TIMEOUT,
                    accept_language='ru,en;q=0.8'):
    async with _request(url, params=params, data=data, timeout=timeout,
                        headers={'Accept-Language': accept_language}) as resp:
        return await resp.text()


async def wikipedia_search(query, lang, limit=5):
    """Поиск статей. Возвращает {lang, totalhits, suggestion, results}.

    totalhits/suggestion приходят из searchinfo — по ним карточка пишет
    «найдено ~N» и подсказку при опечатке."""
    payload = await _get_json(_API.format(lang=lang), {
        'action': 'query',
        'list': 'search',
        'srsearch': query,
        'format': 'json',
        'utf8': 1,
        'srlimit': max(1, min(int(limit), 10)),
        'srinfo': 'suggestion|totalhits',
        'srprop': 'snippet|wordcount|timestamp',
    })
    block = payload.get('query') or {}
    info = block.get('searchinfo') or {}
    results = []
    for item in block.get('search') or []:
        title = (item.get('title') or '').strip()
        if not title:
            continue
        results.append({
            'title': title,
            'snippet': clean_snippet(item.get('snippet') or ''),
            'url': page_url(title, lang),
        })
    try:
        total = int(info.get('totalhits') or 0)
    except (TypeError, ValueError):
        total = 0
    return {
        'lang': lang,
        'totalhits': total,
        'suggestion': (info.get('suggestion') or '').strip(),
        'results': results,
    }


async def wikipedia_summary(title, lang, timeout=10):
    """Короткая выжимка по статье (REST summary): первая пара предложений.

    Дизамбигуацию пропускаем — «Клин» может значить что угодно, выжимка
    там бесполезна. Ошибки сети не критичны: карточка покажет просто список."""
    url = _SUMMARY.format(lang=lang,
                          title=quote((title or '').replace(' ', '_'), safe=''))
    try:
        payload = await _get_json(url, timeout=timeout)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get('type') == 'disambiguation':
        return None
    extract = truncate_words((payload.get('extract') or '').strip(), _ANSWER_LIMIT)
    if not extract:
        return None
    return {
        'extract': extract,
        'description': (payload.get('description') or '').strip(),
        'url': page_url(title, lang),
    }


# ── веб-поиск: DuckDuckGo ───────────────────────────────────────────────────
# У DDG нет бесплатного JSON API, зато HTML-версия отдаёт готовую выдачу.
# Разметка стабильная: блок результата, ссылка class="result__a", сниппет
# class="result__snippet". Парсим её регулярками, без новых зависимостей.

_DDG_BLOCK = '<div class="result results_links'
_DDG_LINK = re.compile(r'class="result__a"\s+href="([^"]+)"')
_DDG_TITLE = re.compile(r'class="result__a"[^>]*>(.*?)</a>', re.S)
_DDG_SNIPPET = re.compile(
    r'class="result__snippet"[^>]*>(.*?)</(?:a|div|span)>', re.S)


def unwrap_ddg_url(url):
    """Ссылка из выдачи DDG → настоящий адрес.

    Часть ссылок DDG оборачивает в /l/?uddg=<url>, а иконки отдаёт без
    протокола (//duckduckgo.com/...). Не-http ссылки отбрасываем."""
    url = (url or '').strip()
    if url.startswith('//'):
        url = 'https:' + url
    if 'uddg=' in url:
        found = parse_qs(urlsplit(url).query).get('uddg')
        if found:
            url = found[0]
    return url if url.startswith(('http://', 'https://')) else ''


def _domain(url):
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith('www.') else host


def parse_ddg_html(body, limit=5):
    """HTML-выдача DuckDuckGo → [{title, url, snippet, domain}].

    Рекламу (`result--ad`, `y.js?ad_domain`) пропускаем, дубли по URL — тоже,
    иначе один и тот же сайт занимает всю карточку."""
    results, seen = [], set()
    for block in (body or '').split(_DDG_BLOCK)[1:]:
        href = _DDG_LINK.search(block)
        if not href or 'result--ad' in block or 'ad_domain=' in href.group(1):
            continue
        url = unwrap_ddg_url(href.group(1))
        if not url or url in seen:
            continue
        title_match = _DDG_TITLE.search(block)
        title = _plain(title_match.group(1)) if title_match else ''
        if not title:
            continue
        snippet = _DDG_SNIPPET.search(block)
        seen.add(url)
        results.append({
            'title': title,
            'url': url,
            'snippet': clean_snippet(snippet.group(1)) if snippet else '',
            'domain': _domain(url),
        })
        if len(results) >= limit:
            break
    return results


async def duckduckgo_search(query, limit=5):
    """Веб-поиск DuckDuckGo. Регион — по языку запроса."""
    lang = detect_lang(query)
    body = await _get_text(_WEB, data={
        'q': query,
        'kl': 'ru-ru' if lang == 'ru' else 'us-en',
    })
    return {'lang': lang, 'results': parse_ddg_html(body, limit)}


# ── новости: Google News RSS ────────────────────────────────────────────────
# news.js у DDG отдаёт 403, а iar=news HTML-эндпоинт игнорирует, поэтому
# новости берём из RSS Google — там есть источник и дата публикации.

def news_page_url(query, lang):
    hl = 'ru' if lang == 'ru' else 'en-US'
    gl = 'RU' if lang == 'ru' else 'US'
    return ('https://news.google.com/search?q=' + quote(query or '') +
            f'&hl={hl}&gl={gl}')


def _news_locale(lang):
    if lang == 'ru':
        return {'hl': 'ru', 'gl': 'RU', 'ceid': 'RU:ru'}
    return {'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'}


def _parse_date(text):
    """RFC-822 дата из RSS → aware datetime (или None)."""
    try:
        dt = email.utils.parsedate_to_datetime((text or '').strip())
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_news_rss(xml_text, limit=5):
    """RSS Google News → [{title, url, source, published}].

    ElementTree, а не регулярки: в описании новости лежит целый HTML.
    Битая лента — пустой список, вызывающий покажет «ничего не найдено»."""
    try:
        root = ElementTree.fromstring(xml_text or '')
    except ElementTree.ParseError:
        return []
    items = []
    for node in root.iter('item'):
        title = _plain(node.findtext('title') or '')
        url = (node.findtext('link') or '').strip()
        if not title or not url.startswith('http'):
            continue
        source = _plain(node.findtext('source') or '')
        # Google News склеивает «Заголовок - Источник»; источник и так
        # показываем отдельной строкой, дублировать его в тексте незачем
        if source and title.endswith(' - ' + source):
            title = title[:-len(source) - 3].strip()
        items.append({
            'title': title,
            'url': url,
            'source': source,
            'published': _parse_date(node.findtext('pubDate')),
        })
        if len(items) >= limit:
            break
    return items


async def google_news(query, limit=5):
    lang = detect_lang(query)
    xml_text = await _get_text(_NEWS, params={'q': query,
                                              **_news_locale(lang)})
    return {'lang': lang, 'articles': parse_news_rss(xml_text, limit)}


def relative_time(dt, now=None):
    """Дата новости → «5 мин назад». now передаётся в тестах."""
    if not dt:
        return ''
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = int(max(0, (now - dt).total_seconds()))
    if delta < 60:
        return 'только что'
    if delta < 3600:
        return f'{delta // 60} мин назад'
    if delta < 86400:
        return f'{delta // 3600} ч назад'
    if delta < 7 * 86400:
        return f'{delta // 86400} дн назад'
    return dt.strftime('%d.%m.%Y')


def _fmt_total(total):
    return f'{total:,}'.replace(',', ' ')


def render_results(query, data, answer=None):
    """Карточка с результатами. answer — выжимка по первому результату."""
    lang = data.get('lang') or 'en'
    results = data.get('results') or []
    parts = []
    if answer and answer.get('extract'):
        parts.append(f"📖 **Кратко**\n{answer['extract']}")
    lines = []
    for i, item in enumerate(results, 1):
        title = _link_title(item.get('title') or '?')
        line = f"{i}. [{title}]({item.get('url')})"
        if i == 1 and answer and answer.get('description'):
            line += f" — {clean_query(answer['description'])}"
        # если выжимка уже пересказала первую статью, её сниппет — тот же
        # текст второй раз: карточка только длиннее, а нового ничего нет
        snippet = ''
        if not (i == 1 and answer and answer.get('extract')):
            snippet = highlight_terms(item.get('snippet') or '', query)
        if snippet:
            line += f"\n   {snippet}"
        lines.append(line)
    if lines:
        parts.append("\n".join(lines))
    suggestion = clean_query(data.get('suggestion') or '')
    if suggestion:
        parts.append("💡 Возможно, вы имели в виду: "
                     f"[{suggestion}]({search_page_url(suggestion, lang)})")
    footer = []
    total = data.get('totalhits') or 0
    if total:
        footer.append(f"найдено ~{_fmt_total(total)}")
    footer.append(f"Wikipedia · {lang.upper()}")
    return ui.card(
        f"🔍 Wikipedia: {clean_query(query)}",
        "\n\n".join(parts) or "—",
        hint="`!search <запрос>` — новый поиск",
        footer=" · ".join(footer),
    )


def render_empty(query, lang):
    """Ничего не найдено: ссылка на поиск в вики, а не тупик."""
    return ui.err(
        f"По запросу **{clean_query(query)}** ничего не найдено.\n\n"
        f"[Открыть поиск в Wikipedia]({search_page_url(query, lang)})",
        title="Wikipedia",
        hint="Попробуйте другие слова или латинское написание",
    )


def render_web(query, data, from_wiki=False):
    """Карточка веб-выдачи DuckDuckGo.

    from_wiki=True — фолбэк после пустой Wikipedia, чтобы было видно,
    почему показан не тот источник."""
    lang = data.get('lang') or 'en'
    results = data.get('results') or []
    if not results:
        return ui.err(
            f"По запросу **{clean_query(query)}** ничего не найдено.\n\n"
            "[Открыть поиск в DuckDuckGo]"
            f"({_WEB_PAGE.format(query=quote(query))})",
            title='Веб-поиск',
            hint='Попробуйте переформулировать запрос',
        )
    lines = []
    for i, item in enumerate(results, 1):
        line = (f"{i}. [{_link_title(item.get('title') or '?')}]"
                f"({_link_url(item['url'])})")
        if item.get('domain'):
            line += f" · {item['domain']}"
        snippet = highlight_terms(item.get('snippet') or '', query)
        if snippet:
            line += f"\n   {snippet}"
        lines.append(line)
    parts = ['\n'.join(lines)]
    if from_wiki:
        parts.insert(0, 'В Wikipedia статьи нет — вот что есть в вебе.')
    return ui.card(
        f"🌐 Веб: {clean_query(query)}",
        '\n\n'.join(parts),
        hint='`!search news <запрос>` — свежие новости',
        footer=f'DuckDuckGo · {lang.upper()}',
    )


def render_news(query, data):
    """Карточка новостей: заголовок, источник и как давно опубликовано."""
    lang = data.get('lang') or 'en'
    articles = data.get('articles') or []
    if not articles:
        return ui.err(
            f"Свежих новостей по запросу **{clean_query(query)}** "
            "не нашлось.\n\n"
            f"[Открыть Google Новости]({news_page_url(query, lang)})",
            title='Новости',
            hint='Попробуйте другие слова',
        )
    lines = []
    for i, item in enumerate(articles, 1):
        line = (f"{i}. [{_link_title(item.get('title') or '?')}]"
                f"({_link_url(item['url'])})")
        meta = ' · '.join(x for x in (item.get('source'),
                                      relative_time(item.get('published')))
                         if x)
        if meta:
            line += f"\n   _{clean_query(meta)}_"
        lines.append(line)
    return ui.card(
        f"📰 Новости: {clean_query(query)}",
        '\n'.join(lines),
        hint='`!search <запрос>` — Wikipedia',
        footer=f'Google News · {lang.upper()}',
    )
