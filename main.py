import os
import logging
from telethon import TelegramClient, events
from flask import Flask
from threading import Thread

# Настройка логов, чтобы видеть, что происходит
logging.basicConfig(level=logging.INFO)

# Получаем данные из настроек Render (Environment Variables)
api_id = os.environ.get('API_ID')
api_hash = os.environ.get('API_HASH')
phone = os.environ.get('PHONE')

# Имя файла сессии (создастся локально как my_userbot.session)
client = TelegramClient('my_userbot', int(api_id), api_hash)

# Flask сервер для "пингов" от UptimeRobot
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
    # Запускаем веб-часть
    Thread(target=run_web).start()
    
    # Запускаем бота
    print("Запуск клиента...")
    client.start(phone=phone)
    print("Бот успешно запущен!")
    client.run_until_disconnected()
