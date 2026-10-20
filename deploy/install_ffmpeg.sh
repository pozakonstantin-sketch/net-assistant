#!/bin/bash
# Устанавливает ffmpeg (статическая сборка с GitHub) — нужен для перевода
# синтезированной речи в OGG/Opus (голосовые сообщения Telegram).
set -euo pipefail

if command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg уже установлен: $(ffmpeg -version | head -1)"
    exit 0
fi

url="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
echo "скачиваю ffmpeg…"
curl -fL --retry 2 -m 600 -o /tmp/ffmpeg.tar.xz "$url"
mkdir -p /tmp/ffmpeg-x
tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg-x
bin=$(find /tmp/ffmpeg-x -maxdepth 2 -type d -name bin | head -1)
install -m 755 "$bin/ffmpeg" /usr/local/bin/ffmpeg
install -m 755 "$bin/ffprobe" /usr/local/bin/ffprobe
rm -rf /tmp/ffmpeg.tar.xz /tmp/ffmpeg-x
ffmpeg -hide_banner -version | head -1
