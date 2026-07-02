#!/bin/bash
# Скачиваем статический ffmpeg для Linux
curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o ffmpeg.tar.xz
tar -xf ffmpeg.tar.xz
mv ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/
chmod +x /usr/local/bin/ffmpeg
# Устанавливаем зависимости
pip install -r requirements.txt
