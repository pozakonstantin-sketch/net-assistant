#!/bin/sh
# Не даём рабочему столу ВМ гаснуть и блокироваться: ИИ должен иметь доступ
# к экрану в любой момент. Запускается при входе и из setup_vm_desktop.sh.
export DISPLAY="${DISPLAY:-:0}"

xset s off 2>/dev/null || true
xset -dpms 2>/dev/null || true
xset s noblank 2>/dev/null || true

gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true

kwriteconfig5 --file kscreenlockerrc --group Daemon --key Autolock false 2>/dev/null || true
kwriteconfig5 --file kscreenlockerrc --group Daemon --key Timeout 0 2>/dev/null || true

exit 0
