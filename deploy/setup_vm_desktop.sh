#!/usr/bin/env bash
# Готовит рабочий стол ВМ для ИИ: автологин в fly-dm, python-xlib,
# systemd-юнит агента X11 (скриншоты, клики, ввод).
set -euo pipefail

INSTALL_DIR=/opt/net-assistant
SERVICE_USER=${SERVICE_USER:-assistant}
UNIT=net-assistant-vm-desktop.service
VENV="$INSTALL_DIR/.venv"

echo "== python-xlib =="
"$VENV/bin/pip" install -q python-xlib

echo "== автологин fly-dm =="
RC=/etc/X11/fly-dm/fly-dmrc
if [ -f "$RC" ]; then
    cp -a "$RC" "$RC.bak-$(date +%Y%m%d)" 2>/dev/null || true
    sed -i \
        -e 's/^AutoLoginEnable=.*/AutoLoginEnable=true/' \
        -e "s/^AutoLoginUser=.*/AutoLoginUser=${SERVICE_USER}/" \
        -e 's/^AutoLoginAgain=.*/AutoLoginAgain=true/' \
        "$RC"
    grep -E '^AutoLogin' "$RC" || true
else
    echo "fly-dm не найден — автологин не настроен"
fi

echo "== systemd =="
install -m 644 "$INSTALL_DIR/deploy/$UNIT" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$UNIT"

echo "== защита от блокировки экрана =="
install -m 755 "$INSTALL_DIR/deploy/vm_nolock.sh" /usr/local/bin/vm_nolock.sh
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "/home/$SERVICE_USER/.config/autostart"
cat > "/home/$SERVICE_USER/.config/autostart/net-assistant-nolock.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Net Assistant — не блокировать экран
Exec=/usr/local/bin/vm_nolock.sh
X-GNOME-Autostart-enabled=true
EOF
chown "$SERVICE_USER:$SERVICE_USER" "/home/$SERVICE_USER/.config/autostart/net-assistant-nolock.desktop"
if [ -e /tmp/.X11-unix/X0 ]; then
    sudo -u "$SERVICE_USER" env DISPLAY=:0 \
        XAUTHORITY="/home/$SERVICE_USER/.Xauthority" /usr/local/bin/vm_nolock.sh || true
fi
install -m 644 "$INSTALL_DIR/deploy/net-assistant-unlock.service" /etc/systemd/system/
install -m 644 "$INSTALL_DIR/deploy/net-assistant-unlock.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now net-assistant-unlock.timer

if [ -e /tmp/.X11-unix/X0 ]; then
    systemctl restart "$UNIT"
    sleep 2
    systemctl --no-pager --lines=5 status "$UNIT" || true
else
    echo "Графическая сессия ещё не запущена — агент стартует после входа в систему."
fi
