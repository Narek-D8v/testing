from telethon import events
from client import client
from core import state, db, owner_filter, respond
from rp_commands import get_all_categories, get_category_commands
import ui

CMD_DESCS = {
    'sleep':       {'desc': 'Включить автоответчик', 'syntax': '!sleep', 'example': '!sleep'},
    'wake':        {'desc': 'Выключить автоответчик', 'syntax': '!wake', 'example': '!wake'},
    'setreply':    {'desc': 'Установить текст ответа', 'syntax': '!setreply [@user|ID|default] [текст]', 'example': '!setreply @username Привет'},
    'menu':         {'desc': 'Меню с кнопками в этом чате (Telegram Business)', 'syntax': '!menu', 'example': '!menu'},
    'status':      {'desc': 'Полный статус: режимы, слежка, шаг опроса ONWATCH', 'syntax': '!status', 'example': '!status'},
    'time':        {'desc': 'Текущее время и дата', 'syntax': '!time', 'example': '!time'},
    'ping':        {'desc': 'Проверить задержку', 'syntax': '!ping', 'example': '!ping'},
    'id':          {'desc': 'ID чата / пользователя', 'syntax': '!id [@username|ID]', 'example': '!id'},
    'info':        {'desc': 'Информация о боте', 'syntax': '!info', 'example': '!info'},
    'restart':     {'desc': 'Перезапустить бота', 'syntax': '!restart', 'example': '!restart'},
    'ghost':       {'desc': 'Ghost-режим (автоудаление команд)', 'syntax': '!ghost', 'example': '!ghost'},
    'resetdata':   {'desc': 'Сбросить все данные ⚠️', 'syntax': '!resetdata', 'example': '!resetdata'},
    'cover':       {'desc': 'Игнор всех команд кроме !cover off', 'syntax': '!cover [on|off]', 'example': '!cover on'},
    'silent':      {'desc': 'Бот молчит в ЛС', 'syntax': '!silent [on|off]', 'example': '!silent on'},
    'shadow':      {'desc': 'Автоудаление ответов через N сек', 'syntax': '!shadow [сек]', 'example': '!shadow 10'},
    'lock':        {'desc': 'Отвечать только контактам', 'syntax': '!lock [on|off]', 'example': '!lock on'},
    'mute':        {'desc': 'Игнорировать все ЛС', 'syntax': '!mute [on|off]', 'example': '!mute on'},
    'typing':      {'desc': 'Показывать «печатает…» перед ответом', 'syntax': '!typing [on|off]', 'example': '!typing on'},
    'autodel':     {'desc': 'Автоудаление всех исходящих сообщений', 'syntax': '!autodel [on|off] [сек]', 'example': '!autodel on 10'},
    'delay':       {'desc': 'Задержка перед автоответом (симуляция человека)', 'syntax': '!delay [сек]', 'example': '!delay 3'},
    'readreceipt': {'desc': 'Отмечать ЛС прочитанными', 'syntax': '!readreceipt [on|off]', 'example': '!readreceipt off'},
    'pmwatch':     {'desc': 'Где выключена слежка за удалениями/правками ЛС', 'syntax': '!pmwatch [mute|unmute] @user|ID', 'example': '!pmwatch mute @username'},
    'status_reset':{'desc': 'Сбросить все стелс-режимы', 'syntax': '!status_reset', 'example': '!status_reset'},
    'me':           {'desc': 'Мой профиль', 'syntax': '!me', 'example': '!me'},
    'avatar':       {'desc': 'Получить аватарку', 'syntax': '!avatar [@username|ID]', 'example': '!avatar'},
    'name':         {'desc': 'Сменить имя', 'syntax': '!name [имя]', 'example': '!name НовоеИмя'},
    'lastname':     {'desc': 'Сменить фамилию', 'syntax': '!lastname [фамилия]', 'example': '!lastname Иванов'},
    'bio':          {'desc': 'Обновить «о себе»', 'syntax': '!bio [текст]', 'example': '!bio Люблю котиков'},
    'whois':        {'desc': 'Информация о пользователе', 'syntax': '!whois @username|ID', 'example': '!whois @durov'},
    'username_check': {'desc': 'Проверить занятость username', 'syntax': '!username_check @username|ID', 'example': '!username_check @test'},
    'dice':         {'desc': 'Кинуть кубик', 'syntax': '!dice', 'example': '!dice'},
    'dart':         {'desc': 'Кинуть дротик', 'syntax': '!dart', 'example': '!dart'},
    'basket':       {'desc': 'Бросить мяч', 'syntax': '!basket', 'example': '!basket'},
    'football':     {'desc': 'Удар по мячу', 'syntax': '!football', 'example': '!football'},
    'bowling':      {'desc': 'Боулинг', 'syntax': '!bowling', 'example': '!bowling'},
    'casino':       {'desc': 'Игровой автомат', 'syntax': '!casino', 'example': '!casino'},
    'coin':         {'desc': 'Подбросить монетку', 'syntax': '!coin', 'example': '!coin'},
    'rand':         {'desc': 'Случайное число', 'syntax': '!rand [a] [b]', 'example': '!rand 1 100'},
    '8ball':        {'desc': 'Магический шар', 'syntax': '!8ball [вопрос]', 'example': '!8ball Сегодня будет удача?'},
    'rps':          {'desc': 'Камень-ножницы-бумага', 'syntax': '!rps [к/н/б]', 'example': '!rps камень'},
    'slot':         {'desc': 'Слот-машина', 'syntax': '!slot', 'example': '!slot'},
    'lucky':        {'desc': 'Индекс удачи', 'syntax': '!lucky', 'example': '!lucky'},
    'choose':       {'desc': 'Случайный выбор', 'syntax': '!choose [вар1, вар2]', 'example': '!choose пицца, суши'},
    'quiz':         {'desc': 'Викторина', 'syntax': '!quiz', 'example': '!quiz'},
    'ytshow':       {'desc': 'Скачать видео с YouTube', 'syntax': '!ytshow <URL> [качество]', 'example': '!ytshow https://youtu.be/... 720'},
    'dl':           {'desc': 'Скачать видео (YouTube / Instagram / TikTok)', 'syntax': '!dl <URL> | !dl (ответом на ссылку)', 'example': '!dl https://www.tiktok.com/@user/video/...'},
    'playlist':     {'desc': 'Скачать плейлист YouTube', 'syntax': '!playlist <URL> [кол-во] | [start-end]', 'example': '!playlist https://youtube.com/playlist?list=... 5'},
    'audio':        {'desc': 'Скачать аудио с YouTube', 'syntax': '!audio <URL> [128|192|320] [voice|file]', 'example': '!audio https://youtu.be/... 192 voice'},
    'sub':          {'desc': 'Скачать субтитры с YouTube', 'syntax': '!sub <URL> [язык]', 'example': '!sub https://youtu.be/... ru'},
    'ytupdate':     {'desc': 'Обновить yt-dlp вручную', 'syntax': '!ytupdate', 'example': '!ytupdate'},
    'calc':         {'desc': 'Калькулятор', 'syntax': '!calc [выражение]', 'example': '!calc 2+2*2'},
    'remind':       {'desc': 'Напоминание', 'syntax': '!remind [сек] [текст]', 'example': '!remind 60 Поставить чайник'},
    'search':       {'desc': 'Поиск в Wikipedia, вебе (DuckDuckGo) или новостях', 'syntax': '!search [web|news] [запрос]', 'example': '!search Пушкин'},
    'shorten':      {'desc': 'Сократить ссылку', 'syntax': '!shorten [url]', 'example': '!shorten https://example.com'},
    'weather':      {'desc': 'Текущая погода в городе', 'syntax': '!weather [город]', 'example': '!weather Moscow'},
    'translate':    {'desc': 'Переводчик (Google)', 'syntax': '!translate [код_языка] [текст]', 'example': '!translate en Привет'},
    'base64':       {'desc': 'Base64 кодирование/декодирование', 'syntax': '!base64 encode|decode [текст]', 'example': '!base64 encode Привет'},
    'hash':         {'desc': 'Хэши (MD5/SHA)', 'syntax': '!hash [текст]', 'example': '!hash password'},
    'morse':        {'desc': 'Азбука Морзе', 'syntax': '!morse [текст]', 'example': '!morse SOS'},
    'caesar':       {'desc': 'Шифр Цезаря', 'syntax': '!caesar encode|decode [сдвиг] [текст]', 'example': '!caesar encode 3 Привет'},
    'vigenere':     {'desc': 'Шифр Виженера', 'syntax': '!vigenere encode|decode [ключ] [текст]', 'example': '!vigenere encode key Привет'},
    'password':     {'desc': 'Генератор паролей', 'syntax': '!password [длина] [simple]', 'example': '!password 20'},
    'qr':           {'desc': 'Сгенерировать QR-код', 'syntax': '!qr [текст]', 'example': '!qr https://example.com'},
    'uuid':         {'desc': 'Генератор UUID', 'syntax': '!uuid', 'example': '!uuid'},
    'color':        {'desc': 'Образец цвета + Hex/RGB', 'syntax': '!color [#HEX или R,G,B]', 'example': '!color #FF0000'},
    'ascii':        {'desc': 'ASCII коды символов', 'syntax': '!ascii [текст]', 'example': '!ascii Hello'},
    'type':         {'desc': 'Печать текста с эффектом', 'syntax': '!type [fast|slow|matrix|glitch] [текст]', 'example': '!type slow Привет'},
    'echo':         {'desc': 'Отправить сообщение', 'syntax': '!echo [текст]', 'example': '!echo Тест'},
    'write':        {'desc': 'Написать в ЛС: markdown — **жирный**, *курсив* / __курсив__, ~~зачёркнутый~~, ||спойлер||, `код`, [ссылка](url); внизу — подпись бота', 'syntax': '!write @user|ID [текст]', 'example': '!write @durov **привет** ||секрет||'},
    'bold':         {'desc': 'Жирный текст', 'syntax': '!bold [текст]', 'example': '!bold Важно'},
    'italic':       {'desc': 'Курсивный текст', 'syntax': '!italic [текст]', 'example': '!italic Цитата'},
    'mono':         {'desc': 'Моноширинный текст', 'syntax': '!mono [текст]', 'example': '!mono code'},
    'clean':        {'desc': 'Удалить свои N сообщений', 'syntax': '!clean [n]', 'example': '!clean 5'},
    'purge':        {'desc': 'Удалить любые N сообщений', 'syntax': '!purge [n]', 'example': '!purge 10'},
    'spam':         {'desc': 'Спам N сообщений', 'syntax': '!spam [n] [текст]', 'example': '!spam 5 Привет'},
    'forward':      {'desc': 'Переслать сообщение в чат', 'syntax': '!forward [@user|ID]', 'example': '!forward -100123456789'},
    'pin':          {'desc': 'Закрепить сообщение', 'syntax': '!pin', 'example': '!pin'},
    'unpin':        {'desc': 'Открепить сообщение', 'syntax': '!unpin', 'example': '!unpin'},
    'copyall':      {'desc': 'Копировать N сообщений в чат', 'syntax': '!copyall [n] [@user|ID]', 'example': '!copyall 50 -100123456789'},
    'react':        {'desc': 'Поставить реакцию', 'syntax': '!react [эмодзи]', 'example': '!react 👍'},
    'save':         {'desc': 'Сохранить значение по ключу (или медиа из ответа)', 'syntax': '!save <ключ> <значение> | !save (ответом на медиа)', 'example': '!save пароль 12345'},
    'get':          {'desc': 'Получить значение по ключу', 'syntax': '!get <ключ>', 'example': '!get пароль'},
    'del':          {'desc': 'Удалить значение по ключу', 'syntax': '!del <ключ>', 'example': '!del пароль'},
    'list':         {'desc': 'Список всех сохранённых данных', 'syntax': '!list', 'example': '!list'},
    'find':         {'desc': 'Поиск по сохранённым данным и заметкам', 'syntax': '!find <слово>', 'example': '!find пароль'},
    'note':         {'desc': 'Сохранить заметку', 'syntax': '!note <название> <текст>', 'example': '!note Идея Купить молоко'},
    'getnote':      {'desc': 'Получить заметку', 'syntax': '!getnote <название>', 'example': '!getnote Идея'},
    'delnote':      {'desc': 'Удалить заметку', 'syntax': '!delnote <название>', 'example': '!delnote Идея'},
    'notes':        {'desc': 'Список всех заметок', 'syntax': '!notes', 'example': '!notes'},
    'todo':         {'desc': 'Добавить задачу', 'syntax': '!todo <текст>', 'example': '!todo Купить молоко'},
    'todos':        {'desc': 'Список всех задач', 'syntax': '!todos', 'example': '!todos'},
    'done':         {'desc': 'Отметить задачу выполненной', 'syntax': '!done <номер>', 'example': '!done 1'},
    'undone':       {'desc': 'Снять отметку выполнения', 'syntax': '!undone <номер>', 'example': '!undone 1'},
    'deltodo':      {'desc': 'Удалить задачу', 'syntax': '!deltodo <номер>', 'example': '!deltodo 1'},
    'afk':          {'desc': 'Включить AFK-режим', 'syntax': '!afk [причина]', 'example': '!afk Сплю'},
    'unafk':        {'desc': 'Выключить AFK-режим', 'syntax': '!unafk', 'example': '!unafk'},
    'chatinfo':     {'desc': 'Информация о чате', 'syntax': '!chatinfo', 'example': '!chatinfo'},
    'members':      {'desc': 'Количество участников', 'syntax': '!members', 'example': '!members'},
    'admins':       {'desc': 'Список администраторов', 'syntax': '!admins', 'example': '!admins'},
    'top':          {'desc': 'Топ активных пользователей', 'syntax': '!top [n]', 'example': '!top 100'},
    'bots':         {'desc': 'Список ботов в чате', 'syntax': '!bots', 'example': '!bots'},
    'sudo':         {'desc': 'Управление sudo-пользователями', 'syntax': '!sudo [on|off] @username|ID', 'example': '!sudo on @durov'},
    'watch':        {'desc': 'Мониторинг новых сессий аккаунта', 'syntax': '!watch [on|off]', 'example': '!watch on'},
    'check_email':  {'desc': 'Проверить email на утечки', 'syntax': '!check_email <email>', 'example': '!check_email test@example.com'},
    'protect':      {'desc': 'Детектор пропавших чатов', 'syntax': '!protect [on|off]', 'example': '!protect on'},
    'onwatch':      {'desc': 'Караулить появление в сети и прислать текст', 'syntax': '!onwatch [-m] @user|ID <текст>', 'example': '!onwatch -m 12345 *привет*'},
    'onwatchlist':  {'desc': 'Список треков: статус цели и время уведомления', 'syntax': '!onwatchlist', 'example': '!onwatchlist'},
    'onwatchstatus': {'desc': 'Детали трека: статус, последний выход, кулдаун, ошибки, причина простоя', 'syntax': '!onwatchstatus @user|ID', 'example': '!onwatchstatus @username'},
    'onwatchlog':   {'desc': 'История сессий: когда заходил и сколько был', 'syntax': '!onwatchlog @user|ID [7d|12h|30m]', 'example': '!onwatchlog @username 7d'},
    'unonwatch':    {'desc': 'Снять отслеживание', 'syntax': '!unonwatch @user|ID', 'example': '!unonwatch @username'},
    'rphelp':       {'desc': 'Список RP-команд', 'syntax': '!rphelp', 'example': '!rphelp'},
    'trhelp':       {'desc': 'Список кодов языков для !translate', 'syntax': '!trhelp', 'example': '!trhelp'},
    'summary':      {'desc': 'Выжимка переписки в 3 предложениях', 'syntax': '!summary @ник [n]', 'example': '!summary @username 50'},
    'vibe':         {'desc': 'Настроение: шкалы, цитаты-доказательства, динамика и советы', 'syntax': '!vibe @ник', 'example': '!vibe @username'},
    'liar':         {'desc': 'Противоречия: цитаты с датами, достоверность, вопросы', 'syntax': '!liar @ник', 'example': '!liar @username'},
    'promises':     {'desc': 'Обещания: невыполненные помнятся между перезапусками', 'syntax': '!promises @ник | !promises done <id> | !promises all', 'example': '!promises @username'},
    'mediation':    {'desc': 'План примирения с готовыми формулировками', 'syntax': '!mediation @ник [мягко|жёстко]', 'example': '!mediation @username мягко'},
    'reality':      {'desc': 'Отделяет факты-цитаты от интерпретаций', 'syntax': '!reality @ник', 'example': '!reality @username'},
    'roles':        {'desc': 'Роли участников чата с уверенностью в процентах', 'syntax': '!roles @ник|ID|чат', 'example': '!roles @username'},
    'sync':         {'desc': 'Сохранить историю переписки в базу для AI', 'syntax': '!sync @ник [кол-во]', 'example': '!sync @username 200'},
    'fact':         {'desc': 'Добавить/очистить факты о человеке', 'syntax': '!fact @ник текст | !fact clear @ник', 'example': '!fact @username любит кофе'},
    'facts':        {'desc': 'Показать факты о человеке', 'syntax': '!facts @ник', 'example': '!facts @username'},
    'rewrite':      {'desc': 'Переписать текст в заданном стиле', 'syntax': '!rewrite [стиль]', 'example': '!rewrite официально'},
    'fix':          {'desc': 'Исправить грамматику и раскладку', 'syntax': '!fix [текст]', 'example': '!fix ghbdtn'},
    'factcheck':    {'desc': 'Проверить факты и нестыковки в тексте', 'syntax': '!factcheck', 'example': '!factcheck'},
    'args':         {'desc': 'Три контраргумента на утверждение', 'syntax': '!args', 'example': '!args'},
    'tr':           {'desc': 'Перевести текст через AI', 'syntax': '!tr [код языка]', 'example': '!tr en'},
    'ocr':          {'desc': 'Распознать текст с картинки', 'syntax': '!ocr (ответом на изображение)', 'example': '!ocr'},
    'tts':          {'desc': 'Озвучить текст голосовым сообщением', 'syntax': '!tts [текст]', 'example': '!tts Привет'},
    'circle':       {'desc': 'Сделать видео-кружочек из видео/гифки', 'syntax': '!circle (ответом на медиа)', 'example': '!circle'},
    'auto':         {'desc': 'AI-автоответчик на пользователя (копирует вашу манеру)', 'syntax': '!auto @ник on|off [кто он]', 'example': '!auto @username on коллега'},
    'style':        {'desc': 'Источник манеры речи для автоответчика', 'syntax': '!style @чат on [N] | !style off', 'example': '!style @chat on 60'},
    'prompt':       {'desc': 'Присылать 3 варианта ответа вместо автоответа', 'syntax': '!prompt @ник on|off', 'example': '!prompt @username on'},
    'blacklist':    {'desc': 'Запретить AI-автоответчик для пользователя', 'syntax': '!blacklist [@ник]', 'example': '!blacklist @username'},
    'whitelist':    {'desc': 'Разрешить AI-автоответчик только этим людям', 'syntax': '!whitelist [@ник]', 'example': '!whitelist @username'},
    'quest':        {'desc': 'Интерактивный квест в ЛС с кнопками', 'syntax': '!quest начать | !quest <действие> | !quest стоп', 'example': '!quest начать'},
    'helpai':       {'desc': 'Справка по AI-командам', 'syntax': '!helpai [раздел|all]', 'example': '!helpai all'},
}

# Порядок здесь → порядок разделов в `!help` и меню. Команды одной темы
# (онлайн-караул + слежка за ЛС) держим рядом: `!pmwatch` — это игнор-лист
# той же слежки, а не отдельная «безопасность».
COMMANDS_LIST = {
    'основные': [
        '!sleep', '!wake', '!setreply', '!status', '!time', '!ping',
        '!id', '!info', '!menu', '!restart', '!ghost', '!resetdata'
    ],
    'онлайн': [
        '!onwatch', '!onwatchlist', '!onwatchstatus', '!onwatchlog',
        '!unonwatch', '!pmwatch'
    ],
    'ии': [
        '!summary', '!vibe', '!liar', '!promises', '!mediation',
        '!reality', '!roles', '!sync', '!fact', '!facts',
        '!rewrite', '!fix', '!factcheck', '!args', '!tr',
        '!ocr', '!tts', '!circle',
        '!auto', '!style', '!prompt', '!blacklist', '!whitelist',
        '!quest', '!helpai'
    ],
    'заметки': [
        '!save', '!get', '!del', '!list', '!find',
        '!note', '!getnote', '!delnote', '!notes',
        '!todo', '!todos', '!done', '!undone', '!deltodo'
    ],
    'сообщения': [
        '!type', '!echo', '!bold', '!italic', '!mono',
        '!clean', '!purge', '!spam', '!forward', '!pin', '!unpin',
        '!copyall', '!react'
    ],
    'утилиты': [
        '!calc', '!remind', '!search', '!shorten', '!weather', '!translate', '!trhelp',
        '!base64', '!hash', '!morse', '!caesar', '!vigenere', '!password',
        '!qr', '!uuid', '!color', '!ascii', '!write'
    ],
    'youtube': [
        '!ytshow', '!dl', '!playlist', '!audio', '!sub', '!ytupdate'
    ],
    'стелс': [
        '!cover', '!silent', '!shadow', '!lock', '!mute',
        '!typing', '!autodel', '!delay', '!readreceipt', '!status_reset'
    ],
    'профиль': [
        '!me', '!avatar', '!name', '!lastname', '!bio', '!whois', '!username_check'
    ],
    'инфо': ['!chatinfo', '!members', '!admins', '!top', '!bots'],
    'afk': ['!afk', '!unafk'],
    'безопасность': [
        '!sudo', '!watch', '!check_email', '!protect'
    ],
    'игры': [
        '!dice', '!dart', '!basket', '!football', '!bowling', '!casino',
        '!coin', '!rand', '!8ball', '!rps', '!slot', '!lucky', '!choose', '!quiz'
    ],
    'rp': ['!rphelp'],
}

EMOJI_MAP = {
    'основные': '⚙️', 'стелс': '🛡️', 'профиль': '👤', 'игры': '🎮',
    'youtube': '🎬', 'утилиты': '🛠', 'сообщения': '✉️', 'заметки': '📦',
    'онлайн': '⚡', 'безопасность': '🔐', 'afk': '😴', 'инфо': '📊', 'rp': '🎭',
    'ии': '🤖',
}

# Группировка внутри раздела: подпись → команды. Нужна там, где плоский
# список читается хуже (заметки, утилиты, сообщения). Команды вне групп
# выводятся одним блоком в порядке COMMANDS_LIST.
CAT_GROUPS = {
    'ии': [
        ('Анализ переписки', ['!summary', '!vibe', '!liar', '!promises',
                              '!mediation', '!reality', '!roles', '!sync',
                              '!fact', '!facts']),
        ('Редактура', ['!rewrite', '!fix', '!factcheck', '!args', '!tr']),
        ('Медиа', ['!ocr', '!tts', '!circle']),
        ('Автоответчик', ['!auto', '!style', '!prompt',
                          '!blacklist', '!whitelist']),
        ('Квест и справка', ['!quest', '!helpai']),
    ],
    'онлайн': [
        ('Треки в сети', ['!onwatch', '!onwatchlist', '!onwatchstatus',
                          '!onwatchlog', '!unonwatch']),
        ('Слежка за сообщениями в ЛС', ['!pmwatch']),
    ],
    'заметки': [
        ('Хранилище', ['!save', '!get', '!del', '!list', '!find']),
        ('Заметки', ['!note', '!getnote', '!delnote', '!notes']),
        ('TODO', ['!todo', '!todos', '!done', '!undone', '!deltodo']),
    ],
    'сообщения': [
        ('Текст', ['!type', '!echo', '!bold', '!italic', '!mono']),
        ('Управление чатом', ['!clean', '!purge', '!spam', '!forward',
                              '!pin', '!unpin', '!copyall', '!react']),
    ],
    'утилиты': [
        ('Расчёты и справки', ['!calc', '!remind', '!search', '!shorten',
                               '!weather', '!translate', '!trhelp']),
        ('Кодирование', ['!base64', '!hash', '!morse', '!caesar',
                         '!vigenere', '!password']),
        ('Генераторы', ['!qr', '!uuid', '!color', '!ascii']),
        ('Связь', ['!write']),
    ],
    'игры': [
        ('Кубики и анимации', ['!dice', '!dart', '!basket', '!football',
                               '!bowling', '!casino']),
        ('На удачу', ['!coin', '!rand', '!8ball', '!rps', '!slot',
                      '!lucky', '!choose', '!quiz']),
    ],
}

CAT_INTRO = {
    'ии': (
        "AI-помощник работает на Gemini (резерв — OpenRouter); ключи задаются в .env.\n"
        "История ЛС пишется в отдельную таблицу с ретеншном и не влияет на анти-удаление.\n"
        "Автоответчик отвечает от вашего имени, копируя вашу манеру речи; собеседник — это описание человека, а не роль бота.\n"
        "Квест отправляется через Telegram Business в ЛС от вашего имени и управляется кнопками."
    ),
    'онлайн': (
        "Трек присылает цели текст, когда она появляется в сети, и уведомляет вас в боте.\n"
        "Строгий режим: уведомление при каждом входе в сеть — даже если цель была оффлайн 2 секунды. Любой выход разрывает сессию; пока выхода не было, typing/сообщения/галочки глушат повторы.\n"
        "Ловит также смену профиля, сторис, реакцию, звонок и правки в ЛС.\n"
        "`-m` — markdown в тексте; повторный `!onwatch` меняет текст и начинает сессию заново."
    ),
    'youtube': "Скачивание видео, музыки и субтитров; одна загрузка — до 10 минут.",
    'заметки': "`!save` в ответ на фото/видео сохраняет медиа, а не значение.",
    'сообщения': "Массовые действия считают сообщения текущего чата; медиа сохраняется через `!save`.",
    'безопасность': "`!watch` и `!protect` переживают рестарт; уведомления приходят во внешнего бота.",
    'rp': (
        "Ответьте (reply) на сообщение одним словом — бот отправит действие.\n"
        "Через Enter после слова можно добавить свою реплику.\n"
        "Пример: `обнять` + Enter + `Ты моя сладкая` → «🤗 ...обнял... Ты моя сладкая»."
    ),
}

CAT_OUTRO = {
    'онлайн': (
        "⚠️ Караул работает только в ЛС. Если трек перестал опрашиваться "
        "(аккаунт удалён или его нет в кеше сессии), владелец один раз получает "
        "уведомление, а `!onwatchstatus` показывает причину и её возраст."
    ),
    'rp': "**Категории RP:**\n" + "\n".join(
        f"• {cat.capitalize()}: {', '.join(get_category_commands(cat))}"
        for cat in get_all_categories()
    ),
}


def _cmd_line(cmd):
    """Одна команда одной строкой: синтаксис — описание из CMD_DESCS."""
    d = CMD_DESCS.get(cmd.lstrip('!'))
    if not d:
        return f"`{cmd}`"
    return f"`{d['syntax']}` — {d['desc']}"


def _section_body(cat):
    """Текст раздела: вступление → команды по группам → примечание.

    Описания берутся из CMD_DESCS, поэтому карточка и `!help cmd` не расходятся.
    """
    blocks = []
    intro = (CAT_INTRO.get(cat) or '').strip()
    if intro:
        blocks.append(intro)
    groups = CAT_GROUPS.get(cat) or [(None, COMMANDS_LIST.get(cat, []))]
    for label, cmds in groups:
        lines = [f"**{label}**"] if label else []
        lines += [_cmd_line(c) for c in cmds]
        if lines:
            blocks.append("\n".join(lines))
    outro = (CAT_OUTRO.get(cat) or '').strip()
    if outro:
        blocks.append(outro)
    return "\n\n".join(blocks)


def _category_of(name):
    """Раздел, в котором лежит команда (для `!help cmd`)."""
    key = f"!{name}"
    for cat, cmds in COMMANDS_LIST.items():
        if key in cmds:
            return cat
    return None


# Совместимость: раньше HELP_CATS был словарём готовых текстов разделов.
HELP_CATS = {cat: _section_body(cat) for cat in COMMANDS_LIST}

HELP_TITLES = {
    'основные': 'Основные',
    'онлайн': 'Онлайн и слежка',
    'заметки': 'Заметки и TODO',
    'сообщения': 'Сообщения',
    'утилиты': 'Утилиты',
    'youtube': 'Загрузка медиа',
    'стелс': 'Стелс-режимы',
    'профиль': 'Профиль',
    'инфо': 'Информация о чате',
    'afk': 'AFK',
    'безопасность': 'Безопасность',
    'ии': 'AI-помощник',
    'игры': 'Игры и развлечения',
    'rp': 'RP-команды',
}


# ── Внешний бот: меню с инлайн-кнопками (пульт + справка) ──
# Тексты — те же карточки ui.*, но отправляются Bot API (HTML через ui.to_html).

def _b(text, data):
    return {"text": text, "callback_data": data}


def _mark(v):
    return "✅" if v else "❌"


def menu_view(last_action=None):
    """Главное меню бота: состояние режимов + пульт + разделы справки."""
    modes = ui.rows(
        ("Ghost", _mark(state.ghost_mode), "👻"),
        ("Cover", _mark(state.cover_enabled), "🛡️"),
        ("Silent", _mark(state.silent_enabled), "🤐"),
        ("Mute", _mark(state.mute_enabled), "🔇"),
        ("Lock", _mark(state.lock_enabled), "🔒"),
    )
    body = f"Последнее действие: _{last_action}_\n\n{modes}" if last_action else modes
    text = ui.card("🎮 Меню UserBot", body,
                   hint="Кнопки: пульт сверху, разделы справки ниже. `!menu` — переслать меню ещё раз.")
    cats = list(HELP_CATS.keys())
    # В кнопках — нормальные названия разделов, а не ключи словаря.
    cat_rows = [[_b(f"{EMOJI_MAP.get(c, '•')} {HELP_TITLES.get(c, c)}", f"h:{c}")
                 for c in cats[i:i + 2]]
                for i in range(0, len(cats), 2)]
    buttons = [
        [_b("📊 Статус", "c:status"), _b("🏓 Пинг", "c:ping")],
        [_b(f"👻 Ghost {_mark(state.ghost_mode)}", "c:ghost"),
         _b(f"🛡️ Cover {_mark(state.cover_enabled)}", "c:cover")],
        [_b(f"🤐 Silent {_mark(state.silent_enabled)}", "c:silent"),
         _b(f"🔇 Mute {_mark(state.mute_enabled)}", "c:mute"),
         _b(f"🔒 Lock {_mark(state.lock_enabled)}", "c:lock")],
        [_b("📋 Все команды", "h:all")],
    ] + cat_rows
    return text, buttons


def _all_blocks():
    """Блоки «раздел → команды с описаниями» для `!help all`."""
    for cat, cmds in COMMANDS_LIST.items():
        title = f"{EMOJI_MAP.get(cat, '•')} **{HELP_TITLES.get(cat, cat.capitalize())}**"
        yield title + "\n" + "\n".join(_cmd_line(c) for c in cmds)


def _all_commands_body():
    """Компактный указатель для меню: только имена команд."""
    parts = []
    for cat, cmds in COMMANDS_LIST.items():
        emoji = EMOJI_MAP.get(cat, '•')
        parts.append(f"{emoji} **{HELP_TITLES.get(cat, cat.capitalize())}**\n"
                     + ", ".join(f"`{c}`" for c in cmds))
    return "\n\n".join(parts)


def _split_lines(text, limit):
    """Режет слишком длинный блок по строкам — лимит сообщения Telegram."""
    parts, cur = [], ''
    for line in text.split("\n"):
        if cur and len(cur) + 1 + len(line) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = line if not cur else cur + "\n" + line
    if cur:
        parts.append(cur)
    return parts


def _message_chunks(blocks, limit=3800):
    """Собирает блоки в сообщения, не превышающие лимит Telegram (4096)."""
    chunks, cur = [], ''
    for block in blocks:
        parts = [block] if len(block) <= limit else _split_lines(block, limit)
        for part in parts:
            if cur and len(cur) + 2 + len(part) > limit:
                chunks.append(cur)
                cur = part
            else:
                cur = part if not cur else cur + "\n\n" + part
    if cur:
        chunks.append(cur)
    return chunks


def _category_body(cat):
    return HELP_CATS[cat]


def help_view(data):
    """callback_data 'h:*' → (текст-карточка, кнопки) для editMessageText."""
    if data == 'h:all':
        return (ui.card("📋 Все команды", _all_commands_body(),
                        footer="◀️ Меню — назад"),
                [[_b("◀️ Меню", "h:back")]])
    cat = (data or '')[2:] if data and data.startswith('h:') else ''
    if cat in HELP_CATS:
        text = ui.card(
            f"{EMOJI_MAP.get(cat, '•')} {HELP_TITLES.get(cat, cat.capitalize())}",
            _category_body(cat),
            footer="◀️ Меню — назад · `!help all` — все команды",
        )
        buttons = [
            [_b("◀️ Меню", "h:back"), _b("📋 Все команды", "h:all")],
            [_b("📊 Статус", "c:status"), _b("🏓 Пинг", "c:ping")],
        ]
        return text, buttons
    return menu_view()


@client.on(events.NewMessage(pattern=r'!help(?:\s+(.+))?$', func=owner_filter))
async def help_cmd(e):
    arg = (e.pattern_match.group(1) or '').strip().lower()
    cats_line = " · ".join(f"`!help {c}`" for c in HELP_CATS)

    if arg == 'cmd':
        await respond(e, ui.usage("!help cmd <команда>", title="Справка по команде"))
        db.bump_stat('cmds')
        return

    if arg.startswith('cmd '):
        cmd_name = arg[4:].strip().lstrip('!')
        if cmd_name in CMD_DESCS:
            d = CMD_DESCS[cmd_name]
            cat = _category_of(cmd_name)
            section = (f"{EMOJI_MAP.get(cat, '•')} {HELP_TITLES.get(cat, cat)}"
                       if cat else '—')
            await respond(e, ui.card(
                f"📖 !{cmd_name}",
                ui.rows(
                    ("Раздел", section),
                    ("Описание", d['desc']),
                    ("Синтаксис", f"`{d['syntax']}`"),
                    ("Пример", f"`{d['example']}`"),
                ),
                footer="`!help` — разделы · `!help all` — все команды",
            ))
        else:
            await respond(e, ui.err(f"Команда `{cmd_name}` не найдена", title="Справка",
                                    hint="`!help all` — список всех команд"))
        db.bump_stat('cmds')
        return

    if arg == 'all':
        # Описания делают список длиннее лимита одного сообщения, поэтому
        # режем по разделам и шлём несколькими сообщениями (без обрезки).
        chunks = _message_chunks(list(_all_blocks()))
        total = len(chunks)
        for i, chunk in enumerate(chunks, 1):
            head = "📋 **Все команды**" + (f" · {i}/{total}" if total > 1 else '')
            parts = [head, ui.SEP, '', chunk]
            if i == total:
                parts += ['', ui.SEP,
                          "`!help cmd <команда>` — подробнее · `!help <раздел>` — один раздел"]
            text = "\n".join(parts)
            if i == 1:
                await respond(e, text)
            else:
                await e.reply(text, parse_mode='md')
        db.bump_stat('cmds')
        return

    if arg:
        cat = arg
        if cat not in HELP_CATS:
            await respond(e, ui.err(f"Раздел `{cat}` не найден", title="Справка",
                                    hint=f"Разделы: {cats_line}"))
            db.bump_stat('cmds')
            return
        body = _category_body(cat)
        await respond(e, ui.card(
            f"{EMOJI_MAP.get(cat, '•')} {HELP_TITLES.get(cat, cat.capitalize())}",
            body,
            footer="`!help cmd <команда>` — описание одной команды · `!help` — назад",
        ))
        db.bump_stat('cmds')
        return

    total = sum(len(cmds) for cmds in COMMANDS_LIST.values())
    lines = []
    for cat_name, cmds in COMMANDS_LIST.items():
        emoji = EMOJI_MAP.get(cat_name, '•')
        title = HELP_TITLES.get(cat_name, cat_name.capitalize())
        lines.append(f"{emoji} `!help {cat_name}` — {title} · {len(cmds)}")
    await respond(e, ui.card(
        "📚 Справка",
        "\n".join(lines),
        footer=f"Всего {total} команд. `!help cmd <команда>` — подробнее · "
               "`!help all` — все с описаниями",
    ))
    db.bump_stat('cmds')

@client.on(events.NewMessage(pattern=r'!trhelp$', func=owner_filter))
async def trhelp_cmd(e):
    codes = sorted(LANG_CODES)
    chunk_size = 25
    chunks = [codes[i:i+chunk_size] for i in range(0, len(codes), chunk_size)]
    for i, chunk in enumerate(chunks):
        lines = [f"`{c}` — {LANG_CODES[c]}" for c in chunk]
        if i == 0:
            lines.insert(0, ui.SEP)
            lines.insert(0, "**🌐 Коды языков Google Translate**")
            lines.append("\n💡 Использование: `!translate <код> [текст]`")
            await respond(e, "\n".join(lines))
        else:
            await e.reply("\n".join(lines))

LANG_CODES = {
    'af': '🇿🇦 Африкаанс', 'sq': '🇦🇱 Албанский', 'am': '🇪🇹 Амхарский', 'ar': '🇸🇦 Арабский',
    'hy': '🇦🇲 Армянский', 'az': '🇦🇿 Азербайджанский', 'eu': '🇪🇸 Баскский', 'be': '🇧🇾 Белорусский',
    'bn': '🇧🇩 Бенгальский', 'bs': '🇧🇦 Боснийский', 'bg': '🇧🇬 Болгарский', 'ca': '🇪🇸 Каталанский',
    'ceb': '🇵🇭 Себуанский', 'ny': '🇲🇼 Чичева', 'zh': '🇨🇳 Китайский', 'co': '🇫🇷 Корсиканский',
    'hr': '🇭🇷 Хорватский', 'cs': '🇨🇿 Чешский', 'da': '🇩🇰 Датский', 'nl': '🇳🇱 Нидерландский',
    'en': '🇬🇧 Английский', 'eo': '🌍 Эсперанто', 'et': '🇪🇪 Эстонский', 'tl': '🇵🇭 Филиппинский',
    'fi': '🇫🇮 Финский', 'fr': '🇫🇷 Французский', 'fy': '🇳🇱 Фризский', 'gl': '🇪🇸 Галисийский',
    'ka': '🇬🇪 Грузинский', 'de': '🇩🇪 Немецкий', 'el': '🇬🇷 Греческий', 'gu': '🇮🇳 Гуджарати',
    'ht': '🇭🇹 Гаитянский', 'ha': '🇳🇬 Хауса', 'haw': '🌺 Гавайский', 'he': '🇮🇱 Иврит',
    'hi': '🇮🇳 Хинди', 'hmn': '🇨🇳 Хмонг', 'hu': '🇭🇺 Венгерский', 'is': '🇮🇸 Исландский',
    'ig': '🇳🇬 Игбо', 'id': '🇮🇩 Индонезийский', 'ga': '🇮🇪 Ирландский', 'it': '🇮🇹 Итальянский',
    'ja': '🇯🇵 Японский', 'jv': '🇮🇩 Яванский', 'kn': '🇮🇳 Каннада', 'kk': '🇰🇿 Казахский',
    'km': '🇰🇭 Кхмерский', 'rw': '🇷🇦 Киньяруанда', 'ko': '🇰🇷 Корейский', 'ku': '🇮🇶 Курдский',
    'ky': '🇰🇬 Кыргызский', 'lo': '🇱🇦 Лаосский', 'la': '🏛 Латынь', 'lv': '🇱🇻 Латышский',
    'lt': '🇱🇹 Литовский', 'lb': '🇱🇺 Люксембургский', 'mk': '🇲🇰 Македонский', 'mg': '🇲🇬 Малагасийский',
    'ms': '🇲🇾 Малайский', 'ml': '🇮🇳 Малаялам', 'mt': '🇲🇹 Мальтийский', 'mi': '🇳🇿 Маори',
    'mr': '🇮🇳 Маратхи', 'mn': '🇲🇳 Монгольский', 'my': '🇲🇲 Мьянманский', 'ne': '🇳🇵 Непальский',
    'no': '🇳🇴 Норвежский', 'or': '🇮🇳 Ория', 'ps': '🇦🇫 Пушту', 'fa': '🇮🇷 Персидский',
    'pl': '🇵🇱 Польский', 'pt': '🇵🇹 Португальский', 'pa': '🇮🇳 Панджаби', 'ro': '🇷🇴 Румынский',
    'ru': '🇷🇺 Русский', 'sm': '🇼🇸 Самоанский', 'gd': '🏴 Шотландский', 'sr': '🇷🇸 Сербский',
    'st': '🇱🇸 Сесото', 'sn': '🇿🇼 Шона', 'sd': '🇵🇰 Синдхи', 'si': '🇱🇰 Сингальский',
    'sk': '🇸🇰 Словацкий', 'sl': '🇸🇮 Словенский', 'so': '🇸🇴 Сомалийский', 'es': '🇪🇸 Испанский',
    'su': '🇮🇩 Сунданский', 'sw': '🇹🇿 Суахили', 'sv': '🇸🇪 Шведский', 'tg': '🇹🇯 Таджикский',
    'ta': '🇮🇳 Тамильский', 'tt': '🇷🇺 Татарский', 'te': '🇮🇳 Телугу', 'th': '🇹🇭 Тайский',
    'tr': '🇹🇷 Турецкий', 'tk': '🇹🇲 Туркменский', 'uk': '🇺🇦 Украинский', 'ur': '🇵🇰 Урду',
    'ug': '🇨🇳 Уйгурский', 'uz': '🇺🇿 Узбекский', 'vi': '🇻🇳 Вьетнамский', 'cy': '🏴 Валлийский',
    'xh': '🇿🇦 Коса', 'yi': '🇮🇱 Идиш', 'yo': '🇳🇬 Йоруба', 'zu': '🇿🇦 Зулусский',
}
