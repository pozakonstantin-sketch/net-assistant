#!/bin/bash
# Панель по HTTPS в домашней сети — без туннелей, аккаунтов и VPN.
#
# Что делает:
#   1. ставит Caddy (статический бинарник с GitHub), если его нет;
#   2. выпускает сертификат Let's Encrypt для tailnet-имени через `tailscale cert`;
#   3. поднимает Caddy на LAN-адресе ВМ, проксируя на 127.0.0.1:8787;
#   4. ставит таймер продления сертификата;
#   5. добавляет DNS-запись на роутере (OpenWrt/dnsmasq), чтобы имя внутри
#      домашней сети указывало на ВМ, а не на 100.x tailnet-адрес.
#
# Использование:
#   sudo ./deploy/setup_lan_tls.sh                 # домен и IP определит сам
#   sudo ./deploy/setup_lan_tls.sh <домен> <LAN-IP>
#   sudo SKIP_ROUTER_DNS=1 ./deploy/setup_lan_tls.sh
set -euo pipefail

INSTALL_DIR=${INSTALL_DIR:-/opt/net-assistant}
DOMAIN=${1:-}
BIND_IP=${2:-}

if [[ -z "$DOMAIN" ]]; then
    DOMAIN=$(/usr/sbin/tailscale status --json 2>/dev/null \
        | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)
fi
if [[ -z "$DOMAIN" ]]; then
    echo "не удалось определить домен: передайте его аргументом" >&2
    exit 1
fi

if [[ -z "$BIND_IP" ]]; then
    BIND_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
fi
if [[ -z "$BIND_IP" ]]; then
    echo "не удалось определить LAN-адрес: передайте его аргументом" >&2
    exit 1
fi

echo "== домен: $DOMAIN, адрес: $BIND_IP =="

if ! command -v caddy >/dev/null 2>&1; then
    echo "== установка Caddy =="
    url=$(curl -fsSL -m 30 https://api.github.com/repos/caddyserver/caddy/releases/latest \
        | python3 -c 'import sys,json; d=json.load(sys.stdin); print(next(a["browser_download_url"] for a in d["assets"] if a["name"].endswith("linux_amd64.tar.gz")))')
    curl -fL --retry 2 -m 300 -o /tmp/caddy.tar.gz "$url"
    tar -xzf /tmp/caddy.tar.gz -C /tmp caddy
    install -m 755 /tmp/caddy /usr/local/bin/caddy
    rm -f /tmp/caddy /tmp/caddy.tar.gz
fi
caddy version

echo "== сертификат =="
mkdir -p /etc/net-assistant/tls
/usr/sbin/tailscale cert \
    --cert-file /etc/net-assistant/tls/panel.crt \
    --key-file /etc/net-assistant/tls/panel.key \
    "$DOMAIN"
chmod 644 /etc/net-assistant/tls/panel.crt
chmod 600 /etc/net-assistant/tls/panel.key

echo "== конфигурация Caddy =="
mkdir -p /etc/caddy
sed -e "s|__PANEL_DOMAIN__|$DOMAIN|g" -e "s|__PANEL_BIND__|$BIND_IP|g" \
    "$INSTALL_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile

echo "== systemd =="
install -m 755 "$INSTALL_DIR/deploy/net-assistant-cert.sh" /usr/local/bin/net-assistant-cert.sh
install -m 644 "$INSTALL_DIR/deploy/net-assistant-tls.service" /etc/systemd/system/
install -m 644 "$INSTALL_DIR/deploy/net-assistant-cert.service" /etc/systemd/system/
install -m 644 "$INSTALL_DIR/deploy/net-assistant-cert.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now net-assistant-tls.service
systemctl enable --now net-assistant-cert.timer

echo "== проверка =="
sleep 2
curl -sS -m 20 --resolve "$DOMAIN:443:$BIND_IP" -o /dev/null -w "health: %{http_code}\n" "https://$DOMAIN/health"
curl -sS -m 20 --resolve "$DOMAIN:443:$BIND_IP" -o /dev/null -w "app.js: %{http_code}, %{size_download} байт\n" \
    "https://$DOMAIN/static/app.js"

if [[ "${SKIP_ROUTER_DNS:-0}" != "1" ]]; then
    echo "== DNS на роутере =="
    "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/deploy/router_dns_panel.py" "$DOMAIN" "$BIND_IP" || \
        echo "DNS-запись не добавлена — добавьте вручную:" \
             "uci add_list dhcp.@dnsmasq[0].address='/$DOMAIN/$BIND_IP'; uci commit dhcp; /etc/init.d/dnsmasq restart"
fi

echo
echo "Готово. Откройте https://$DOMAIN/ в домашней сети (VPN выключен)."
