#!/usr/bin/env bash
# Установка Tailscale статическими бинарниками — для Astra Linux SE и других
# систем, где нет подходящего apt-репозитория с пакетом tailscale.
#
#   sudo bash deploy/install_tailscale_static.sh
#
# ВАЖНО: при включении узла НЕ передавайте --accept-routes.
# Домашний роутер (OpenWrt) анонсирует подсеть 192.168.1.0/24 и exit-node.
# Приняв эти маршруты, ВМ «перекрывает» собственную LAN-маршрутизацию: SSH и
# ping до неё пропадают сразу после `tailscale up`, а узел уходит в offline.
# Чинить приходится из консоли Hyper-V:
#
#   sudo tailscale set --accept-routes=false --exit-node=
#
set -euo pipefail

VER="${TAILSCALE_VERSION:-1.104.1}"
URL="https://pkgs.tailscale.com/stable/tailscale_${VER}_amd64.tgz"

cd /tmp
curl -fsSL -O "$URL"
tar -xzf "tailscale_${VER}_amd64.tgz"
install -m 755 "tailscale_${VER}_amd64/tailscale"  /usr/sbin/tailscale
install -m 755 "tailscale_${VER}_amd64/tailscaled" /usr/sbin/tailscaled
install -m 644 "tailscale_${VER}_amd64/systemd/tailscaled.service"  /etc/systemd/system/tailscaled.service
install -m 644 "tailscale_${VER}_amd64/systemd/tailscaled.defaults" /etc/default/tailscaled
mkdir -p /var/lib/tailscale
systemctl daemon-reload
systemctl enable --now tailscaled.service

cat <<'EOF'

Установлено. Дальше — включить узел (без --accept-routes!):

  sudo tailscale up --auth-key=tskey-auth-... \
      --hostname=<имя> --accept-dns=false --operator=assistant

Панель (мини-приложение Telegram) по HTTPS внутри тейлнета:

  sudo tailscale serve --bg --https=443 http://127.0.0.1:8787
  sudo tailscale serve status    # → https://<имя>.<тейлнет>.ts.net

Графический трей (необязательно): `tailscale systray`, автозапуск —
/etc/xdg/autostart/tailscale-systray.desktop.
EOF
