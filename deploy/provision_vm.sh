#!/usr/bin/env bash
# Подготовка ВМ-ядра: зависимости, venv, systemd-юниты.
#
#   sudo INSTALL_DIR=/opt/net-assistant bash deploy/provision_vm.sh
#
# Предполагается, что проект уже скопирован в INSTALL_DIR
# (git clone или scp с Windows), а в нём есть .env (или будет создан из примера).
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/net-assistant}"
SERVICE_USER="${SERVICE_USER:-assistant}"
FAKE_AGENTS="${FAKE_AGENTS:-pc router android}"

if [[ $EUID -ne 0 ]]; then
    echo "Нужны права root: sudo bash $0" >&2
    exit 1
fi

if [[ ! -f "$INSTALL_DIR/requirements.txt" ]]; then
    echo "В $INSTALL_DIR нет requirements.txt — сначала скопируйте проект." >&2
    exit 1
fi

echo "== Пакеты =="
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip

echo "== Пользователь $SERVICE_USER =="
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$SERVICE_USER"
    echo "создан пользователь $SERVICE_USER"
else
    echo "пользователь уже есть"
fi

echo "== Владелец каталога =="
# venv создаётся от имени сервисного пользователя, поэтому каталог должен быть
# его ещё до этого шага (иначе Permission denied на $INSTALL_DIR/.venv)
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
# bsdtar из Windows приносит каталоги с режимом 0555 — без w сервис не сможет писать
chmod -R u+rwX,go+rX "$INSTALL_DIR"

echo "== Окружение Python =="
if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
    sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/.venv"
fi
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

echo "== Конфигурация =="
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    chown "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    echo "создан .env из примера — ВПИШИТЕ ASSISTANT_TOKEN и LLM_API_KEY:"
    echo "    sudo nano $INSTALL_DIR/.env"
else
    echo ".env уже есть"
fi
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

echo "== systemd =="
install -m 644 "$INSTALL_DIR/deploy/net-assistant-core.service" /etc/systemd/system/
install -m 644 "$INSTALL_DIR/deploy/net-assistant-router.service" /etc/systemd/system/
install -m 644 "$INSTALL_DIR/deploy/net-assistant-telegram.service" /etc/systemd/system/
install -m 644 "$INSTALL_DIR/deploy/net-assistant-fake@.service" /etc/systemd/system/
install -m 644 "$INSTALL_DIR/deploy/tailscale-watchdog.service" /etc/systemd/system/
install -m 644 "$INSTALL_DIR/deploy/tailscale-watchdog.timer" /etc/systemd/system/
install -m 755 "$INSTALL_DIR/deploy/tailscale_watchdog.py" /usr/local/bin/tailscale_watchdog.py
systemctl daemon-reload
systemctl enable --now net-assistant-core.service

for agent in $FAKE_AGENTS; do
    systemctl enable --now "net-assistant-fake@${agent}.service"
done

echo "== Telegram-бот =="
if grep -q '^TELEGRAM_BOT_TOKEN=.\+' "$INSTALL_DIR/.env"; then
    systemctl enable --now net-assistant-telegram.service
else
    echo "TELEGRAM_BOT_TOKEN не задан — бот не включён (впишите токен в .env)"
fi

echo "== Сторож tailscaled =="
if command -v tailscale >/dev/null 2>&1; then
    systemctl enable --now tailscale-watchdog.timer
else
    echo "Tailscale не найден — сторож не включён"
fi

echo
echo "== Готово =="
systemctl --no-pager --lines=0 status net-assistant-core.service || true
echo
echo "Статус:   systemctl status net-assistant-core"
echo "Логи:     journalctl -u net-assistant-core -f"
echo "Агенты:   systemctl status 'net-assistant-fake@*'"
echo "Сторож:   systemctl status tailscale-watchdog.timer; tail -f /var/log/tailscale-watchdog.log"
echo "Проверка: curl -s http://127.0.0.1:8787/health"
echo
echo "Если правили .env — перезапустите:"
echo "    sudo systemctl restart net-assistant-core 'net-assistant-fake@*'"
