import os
from telethon import TelegramClient, events
from flask import Flask
from threading import Thread

# Берем данные из настроек Render (Environment Variables)
api_id = os.environ.get('API_ID')
api_hash = os.environ.get('API_HASH')

if not api_id or not api_hash:
    raise ValueError("Не найдены API_ID или API_HASH!")

# Создаем клиента
client = TelegramClient('my_userbot', int(api_id), api_hash)

# Веб-сервер для того, чтобы Render не усыплял бота
app = Flask(__name__)
@app.route('/')
def home():
    return "Бот работает!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# Логика бота
@client.on(events.NewMessage(incoming=True))
async def handler(event):
    if event.is_private and not event.sender.is_bot:
        await event.reply('Привет! Я сейчас занят, отвечу позже.')

if __name__ == "__main__":
    # Запускаем веб-часть в фоне
    Thread(target=run_web).start()
    # Запускаем бота
    client.start()
    client.run_until_disconnected()
