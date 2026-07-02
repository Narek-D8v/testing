#!/bin/bash

# Скачиваем статический ffmpeg для Linux
curl -L https://github.com/eugeneware/ffmpeg-static/releases/download/b5.0.1/ffmpeg-linux-x64 -o ffmpeg
chmod +x ffmpeg
mv ffmpeg /usr/local/bin/
