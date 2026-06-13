import os
import logging
import datetime
import asyncio
from telethon import TelegramClient, events
from telethon.tl.types import InputMediaDice
from flask import Flask
from threading import Thread

logging.basicConfig(level=logging.INFO)

api_id = os.environ.get('API_ID')
api_hash = os.environ.get('API_HASH')

auto_reply_enabled = False
client = TelegramClient('my_userbot', int(api_id), api_hash)
app = Flask(__name__)

@app.route('/')
def home():
    return "Бот работает!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- 20 КОМАНД ---

@client.on(events.NewMessage(pattern='/sleep', from_users='me'))
async def c1(e):
    global auto_reply_enabled
    auto_reply_enabled = True
    await e.edit('💤 Автоответчик ВКЛЮЧЕН.')

@client.on(events.NewMessage(pattern='/wake', from_users='me'))
async def c2(e):
    global auto_reply_enabled
    auto_reply_enabled = False
    await e.edit('☀️ Автоответчик ВЫКЛЮЧЕН.')

@client.on(events.NewMessage(pattern='/status', from_users='me'))
async def c3(e):
    status = "💤 Включен" if auto_reply_enabled else "☀️ Выключен"
    await e.edit(f'Бот в сети. Статус: {status}')

@client.on(events.NewMessage(pattern='/time', from_users='me'))
async def c4(e):
    await e.edit(f'⏰ Время: {datetime.datetime.now().strftime("%H:%M:%S")}')

@client.on(events.NewMessage(pattern='/ping', from_users='me'))
async def c5(e):
    await e.edit('🏓 Pong!')

@client.on(events.NewMessage(pattern='/id', from_users='me'))
async def c6(e):
    chat = await e.get_chat()
    await e.edit(f'🆔 ID чата: `{chat.id}`')

@client.on(events.NewMessage(pattern='/clear', from_users='me'))
async def c7(e):
    msg = await e.get_reply_message() or e
    await msg.delete()

@client.on(events.NewMessage(pattern='/info', from_users='me'))
async def c8(e):
    await e.edit('🚀 UserBot 24/7 активен.')

@client.on(events.NewMessage(pattern='/restart', from_users='me'))
async def c9(e):
    await e.edit('🔄 Перезагрузка...')
    os._exit(0)

@client.on(events.NewMessage(pattern='/help', from_users='me'))
async def c10(e):
    await e.edit('Команды: /sleep, /wake, /status, /time, /ping, /id, /clear, /info, /restart, /help, /me, /spam, /type, /dice, /calc, /vol, /search, /rand, /remind, /purge')

@client.on(events.NewMessage(pattern='/me', from_users='me'))
async def c11(e):
    me = await client.get_me()
    await e.edit(f'👤 {me.first_name} | ID: {me.id}')

@client.on(events.NewMessage(pattern='/spam', from_users='me'))
async def c12(e):
    args = e.text.split()
    count = int(args[1]) if len(args) > 1 else 3
    for _ in range(count): await e.respond('🤖 Спам-тест')

@client.on(events.NewMessage(pattern='/type', from_users='me'))
async def c13(e):
    txt = e.text.replace('/type ', '')
    curr = ""
    for char in txt:
        curr += char
        await e.edit(curr + "█")

@client.on(events.NewMessage(pattern='/dice', from_users='me'))
async def c14(e):
    await e.delete()
    await e.client.send_message(e.chat_id, file=InputMediaDice(emoticon='🎲'))

@client.on(events.NewMessage(pattern='/calc', from_users='me'))
async def c15(e):
    try: await e.edit(f'🧮 Результат: {eval(e.text.replace("/calc ", ""))}')
    except: await e.edit('❌ Ошибка')

@client.on(events.NewMessage(pattern='/vol', from_users='me'))
async def c16(e):
    await e.edit('🔊 Громкость установлена на 100%')

@client.on(events.NewMessage(pattern='/search', from_users='me'))
async def c17(e):
    query = e.text.replace('/search ', '')
    await e.edit(f'🔍 Ищу: {query}...\nhttps://google.com/search?q={query.replace(" ", "+")}')

@client.on(events.NewMessage(pattern='/rand', from_users='me'))
async def c18(e):
    import random
    await e.edit(f'🎲 Случайное число: {random.randint(1, 100)}')

@client.on(events.NewMessage(pattern='/remind', from_users='me'))
async def c19(e):
    await e.edit('⏰ Напоминание установлено (через 1 минуту)')
    await asyncio.sleep(60)
    await e.respond('🔔 Напоминание сработало!')

@client.on(events.NewMessage(pattern='/purge', from_users='me'))
async def c20(e):
    await e.delete()
    async for msg in client.iter_messages(e.chat_id, limit=10):
        await msg.delete()

# --- АВТООТВЕТЧИК ---
@client.on(events.NewMessage(incoming=True, private=True))
async def handler(event):
    global auto_reply_enabled
    if auto_reply_enabled:
        sender = await event.get_sender()
        if sender and not getattr(sender, 'bot', False):
            await event.reply('Привет! Я его автоответчик и он сейчас занят, ответит позже.😘')

if __name__ == "__main__":
    Thread(target=run_web).start()
    client.start()
    client.run_until_disconnected()
