#!/bin/bash
apt-get update -qq && apt-get install -y -qq ffmpeg unzip 2>/dev/null

# deno — нужен для yt-dlp (извлечение YouTube подписей)
if ! command -v deno &> /dev/null; then
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh 2>/dev/null
fi

# nodejs — альтернативный JS runtime для yt-dlp
if ! command -v node &> /dev/null; then
  apt-get install -y -qq nodejs 2>/dev/null
fi

pip install -r requirements.txt
