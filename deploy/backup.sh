#!/usr/bin/env bash
# Ежедневный архив: настройки, переписка, журнал, конфиг панели.
# Крупные кэши (venv, озвучка, снимки) в архив не попадают.
set -euo pipefail

INSTALL_DIR=${INSTALL_DIR:-/opt/net-assistant}
DEST=${BACKUP_DIR:-/var/backups/net-assistant}
KEEP=${BACKUP_KEEP:-14}
STAMP=$(date +%Y%m%d-%H%M)

mkdir -p "$DEST"

tar -czf "$DEST/net-assistant-$STAMP.tar.gz" \
    --warning=no-file-changed \
    --exclude="opt/net-assistant/data/tts" \
    --exclude="opt/net-assistant/data/shots" \
    --exclude="opt/net-assistant/data/*.log" \
    -C / \
    "opt/net-assistant/.env" \
    "opt/net-assistant/data" \
    "opt/net-assistant/deploy" \
    "etc/caddy/Caddyfile" \
    2>/dev/null || true

# Оставляем только последние KEEP архивов.
ls -1t "$DEST"/net-assistant-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "архив: $DEST/net-assistant-$STAMP.tar.gz"
ls -lh "$DEST" | tail -5
