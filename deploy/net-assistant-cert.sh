#!/bin/bash
# Обновление сертификата панели (Let's Encrypt через Tailscale)
# и перезагрузка Caddy. Запускается таймером net-assistant-cert.timer.
set -euo pipefail

DIR=/etc/net-assistant/tls
DOMAIN=${PANEL_DOMAIN:-}
if [[ -z "$DOMAIN" ]]; then
    DOMAIN=$(grep -oP '^https://\K[^ {]+' /etc/caddy/Caddyfile | head -1)
fi
if [[ -z "$DOMAIN" ]]; then
    echo "не удалось определить домен панели" >&2
    exit 1
fi

mkdir -p "$DIR"
/usr/sbin/tailscale cert --cert-file "$DIR/panel.crt" --key-file "$DIR/panel.key" "$DOMAIN"
chmod 644 "$DIR/panel.crt"
chmod 600 "$DIR/panel.key"
systemctl reload net-assistant-tls.service
