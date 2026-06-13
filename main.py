import os
import logging
from telethon import TelegramClient, events
from flask import Flask
from threading import Thread

# Настройка логов
logging.basicConfig(level=logging.INFO)

# Получаем данные из переменных окружения
api_id = os.environ.get('API_ID')
api_hash = os.environ.get('API_HASH')
phone = os.environ.get('PHONE')

if not api_id or not api_hash:
    raise ValueError("API_ID и API_HASH должны быть заданы в настройках Render!")

# Инициализация клиента
client = TelegramClient('my_userbot', int(api_id), api_hash)

# Flask сервер для поддержания активности (пинги)
app = Flask(__name__)

@app.route('/')
def home():
    return "Бот работает!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# Исправленная логика обработки сообщений
@client.on(events.NewMessage(incoming=True))
async def handler(event):
    if event.is_private:
        # Получаем отправителя через get_sender()
        sender = await event.get_sender()
        
        # Безопасная проверка: существует ли отправитель и не бот ли он
        # getattr(sender, 'bot', False) защищает от ошибок, если поле отсутствует
        if sender and not getattr(sender, 'bot', False):
            await event.reply('Привет! Я сейчас занят, отвечу позже.')

if __name__ == "__main__":
    # Запускаем веб-сервер
    Thread(target=run_web).start()
    
    # Запуск клиента
    print("Запуск клиента...")
    client.start(phone=phone)
    print("Бот успешно запущен!")
    
    client.run_until_disconnected()
