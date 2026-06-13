import os
import logging
from telethon import TelegramClient, events
from flask import Flask
from threading import Thread

logging.basicConfig(level=logging.INFO)

api_id = os.environ.get('API_ID')
api_hash = os.environ.get('API_HASH')
phone = os.environ.get('PHONE')

# Флаг состояния автоответчика (по умолчанию выключен)
auto_reply_enabled = False

client = TelegramClient('my_userbot', int(api_id), api_hash)
app = Flask(__name__)

@app.route('/')
def home():
    return "Бот работает!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# Команды управления ботом
@client.on(events.NewMessage(pattern='/sleep', from_users='me'))
async def enable_auto(event):
    global auto_reply_enabled
    auto_reply_enabled = True
    await event.edit('Режим автоответчика включен. Бот будет отвечать.')

@client.on(events.NewMessage(pattern='/wake', from_users='me'))
async def disable_auto(event):
    global auto_reply_enabled
    auto_reply_enabled = False
    await event.edit('Режим автоответчика выключен. Бот молчит.')

# Обработка сообщений
@client.on(events.NewMessage(incoming=True))
async def handler(event):
    global auto_reply_enabled
    # Если режим включен и это личка
    if auto_reply_enabled and event.is_private:
        sender = await event.get_sender()
        if sender and not getattr(sender, 'bot', False):
            await event.reply('Привет! Я сейчас занят, отвечу позже.')

if __name__ == "__main__":
    Thread(target=run_web).start()
    client.start(phone=phone)
    client.run_until_disconnected()
