#!/bin/bash
# Туннель без аккаунта через localhost.run.
# Пишет текущий HTTPS-адрес в data/tunnel_url.txt — оттуда его берёт бот,
# чтобы обновить кнопку мини-приложения. При переподключении адрес меняется.
set -u

BASE="${NA_HOME:-/opt/net-assistant}"
URLFILE="${TUNNEL_URL_FILE:-$BASE/data/tunnel_url.txt}"

while true; do
    ssh -tt \
        -o StrictHostKeyChecking=accept-new \
        -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR \
        -o ConnectTimeout=15 \
        -o ServerAliveInterval=20 \
        -o ServerAliveCountMax=3 \
        -o ExitOnForwardFailure=yes \
        -R 80:127.0.0.1:8787 nokey@localhost.run 2>&1 |
    while IFS= read -r line; do
        printf '%s\n' "$line"
        if [[ "$line" =~ (https://[a-z0-9]+\.lhr\.life) ]]; then
            tmp="$URLFILE.tmp"
            printf '%s\n' "${BASH_REMATCH[1]}" > "$tmp" && mv -f "$tmp" "$URLFILE"
        fi
    done
    sleep 5
done
