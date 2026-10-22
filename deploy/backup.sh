#!/usr/bin/env bash
# Резервная копия: настройки, база чата, файлы пользователя.
# Установленные части (venv, инструменты, кэш) не копируем — их создают скрипты.
set -euo pipefail

DEST=${BACKUP_DIR:-/var/backups/net-assistant}
KEEP=${BACKUP_KEEP:-14}
STAMP=$(date +%Y%m%d-%H%M)
TARGET="$DEST/net-assistant-$STAMP.tar.gz"

mkdir -p "$DEST"
# Ручной запуск не должен перемешиваться с таймером.
exec 9>>"$DEST/.lock"
flock -n 9 || exit 0

tar -czf "$TARGET" \
    --warning=no-file-changed \
    --exclude="opt/net-assistant/data/tts" \
    --exclude="opt/net-assistant/data/shots" \
    --exclude="opt/net-assistant/data/*.log" \
    -C / \
    "opt/net-assistant/.env" \
    "opt/net-assistant/data" \
    "opt/net-assistant/deploy" \
    "etc/caddy/Caddyfile"

# Битый или пустой архив не должен оставаться самым свежим: без проверки
# ротация ниже могла удалить все живые копии, оставив только испорченную.
if ! gzip -t "$TARGET" || [ "$(stat -c%s "$TARGET")" -lt 100 ]; then
    rm -f "$TARGET"
    echo "бэкап не прошёл проверку целостности — копии не тронуты" >&2
    exit 1
fi

# Оставляем последние KEEP копий — только после успешного создания новой.
ls -1t "$DEST"/net-assistant-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "Резервная копия: $TARGET"
ls -lh "$DEST" | tail -5
