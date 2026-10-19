"""Агент роутера: SSH + шаблоны команд под конкретную прошивку.

    python -m agents.router_agent

Поддерживается OpenWrt (по умолчанию), а также «сырые» шаблоны для MikroTik,
Keenetic и Asus. Шаблоны можно править под свою модель: см. TEMPLATES.

Важно: команды, меняющие состояние (выключение WiFi, блокировка клиента,
перезагрузка), помечены в ядре как опасные и требуют подтверждения.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from agents.common import AgentClient
from core.config import settings

log = logging.getLogger("router-agent")
FULL_ACCESS = settings.access_mode == "full"

TEMPLATES: dict[str, dict[str, str]] = {
    "openwrt": {
        "uptime": "uptime",
        "board": "cat /tmp/sysinfo/model 2>/dev/null || ubus call system board",
        "wan": "ifstatus wan 2>/dev/null | head -30",
        "lan": "uci get network.lan.ipaddr 2>/dev/null",
        "clients": ("echo '--- leases'; cat /tmp/dhcp.leases 2>/dev/null; "
                    "echo '--- wifi'; iw dev {wifi_if} station dump 2>/dev/null | "
                    "grep -E 'Station|signal:|rx bitrate'"),
        "wifi_off": "uci set wireless.@wifi-device[0].disabled='1'; uci commit wireless; wifi down",
        "wifi_on": "uci set wireless.@wifi-device[0].disabled='0'; uci commit wireless; wifi up",
        "kick": "ubus call hostapd.{wifi_if} del_client "
                '\'{{"addr":"{mac}","deauth":true,"ban_time":60000}}\'',
        "block": ("nft add element inet fw4 blocked_clients '{{ {mac} }}' 2>/dev/null || "
                  "echo 'набор blocked_clients не найден: настройте nft-правило вручную'"),
        "reboot": "reboot",
    },
    "mikrotik": {
        "uptime": "/system resource print",
        "board": "/system identity print; /system routerboard print",
        "wan": "/ip address print",
        "lan": "/ip address print where interface=bridge",
        "clients": "/ip dhcp-server lease print; /interface wireless registration-table print",
        "wifi_off": "/interface wireless disable [find]",
        "wifi_on": "/interface wireless enable [find]",
        "kick": "/interface wireless registration-table remove [find mac-address={mac}]",
        "block": "/ip firewall address-list add list=blocked address={mac}",
        "reboot": "/system reboot",
    },
    # Keenetic и Asus имеют ограниченный CLI; шаблоны заведомо приблизительные.
    "keenetic": {
        "uptime": "show version",
        "board": "show version",
        "wan": "show interface",
        "lan": "show ip interface",
        "clients": "show ip hotspot",
        "wifi_off": "interface Wifi disable",
        "wifi_on": "interface Wifi enable",
        "kick": "no ip hotspot host {mac}",
        "block": "ip hotspot host {mac} deny",
        "reboot": "reboot",
    },
    "asus": {
        "uptime": "uptime",
        "board": "nvram get productid",
        "wan": "nvram get wan0_ipaddr",
        "lan": "nvram get lan_ipaddr",
        "clients": "cat /var/lib/misc/dnsmasq.leases",
        "wifi_off": "radio off",
        "wifi_on": "radio on",
        "kick": "wl -i {wifi_if} deauthenticate {mac}",
        "block": "echo 'блокировка на Asus зависит от прошивки — настройте iptables вручную'",
        "reboot": "reboot",
    },
}


class Router:
    """Одно SSH-соединение, переиспользуется между командами."""

    def __init__(self) -> None:
        self.host = os.getenv("ROUTER_HOST", "192.168.1.1")
        self.port = int(os.getenv("ROUTER_PORT", "22"))
        self.user = os.getenv("ROUTER_USER", "root")
        self.password = os.getenv("ROUTER_PASSWORD") or None
        self.key_path = os.getenv("ROUTER_KEY_PATH") or None
        self.kind = os.getenv("ROUTER_KIND", "openwrt").strip().lower()
        self.wifi_if = os.getenv("ROUTER_WIFI_IF", "wlan0")
        self.timeout = float(os.getenv("ROUTER_TIMEOUT", "20"))
        self._client = None
        self._lock = threading.Lock()

    # ─────────────────────────── SSH ───────────────────────────

    def _connect(self):
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("нужен пакет paramiko: pip install paramiko") from exc

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host, port=self.port, username=self.user,
            password=self.password, key_filename=self.key_path,
            timeout=self.timeout, banner_timeout=self.timeout, auth_timeout=self.timeout,
            look_for_keys=bool(self.key_path), allow_agent=False,
        )
        return client

    def run(self, command: str) -> str:
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._client is None:
                        self._client = self._connect()
                    _, stdout, stderr = self._client.exec_command(command, timeout=self.timeout)
                    output = stdout.read().decode("utf-8", errors="replace")
                    error = stderr.read().decode("utf-8", errors="replace")
                    code = stdout.channel.recv_exit_status()
                    if code != 0 and error.strip():
                        # у части прошивок предупреждения уходят в stderr с кодом 0
                        return f"{output}\n[stderr] {error}".strip()
                    return output.strip()
                except Exception as exc:  # noqa: BLE001 - переподключаемся при обрыве
                    log.warning("SSH %s (попытка %d): %s", self.host, attempt, exc)
                    try:
                        if self._client:
                            self._client.close()
                    finally:
                        self._client = None
                    if attempt == 2:
                        raise RuntimeError(f"не удалось подключиться к {self.host}: {exc}") from exc
        return ""

    def template(self, name: str) -> str:
        table = TEMPLATES.get(self.kind) or TEMPLATES["openwrt"]
        if name not in table:
            raise RuntimeError(f"для прошивки «{self.kind}» нет шаблона «{name}»")
        return table[name]


router = Router()


def _render(name: str, **values: str) -> str:
    return router.template(name).format(wifi_if=router.wifi_if, **values)


# ───────────────────────────── команды ─────────────────────────────


def net_status(_args: dict) -> dict:
    return {
        "host": router.host,
        "kind": router.kind,
        "uptime": router.run(router.template("uptime")),
        "board": router.run(router.template("board"))[:500],
        "wan": router.run(router.template("wan"))[:800],
    }


def router_clients(_args: dict) -> dict:
    output = router.run(_render("clients"))
    return {"host": router.host, "raw": output[:4000],
            "note": "формат вывода зависит от прошивки"}


def router_wifi(args: dict) -> dict:
    enabled = bool(args["enabled"])
    router.run(_render("wifi_on" if enabled else "wifi_off"))
    return {"wifi_enabled": enabled, "note": "если вы в WiFi — связь с роутером прервётся"}


def router_kick(args: dict) -> dict:
    mac = str(args["mac"]).strip().lower()
    if len(mac.split(":")) != 6 and len(mac) != 12:
        raise ValueError("MAC должен быть вида aa:bb:cc:dd:ee:ff")
    result = router.run(_render("kick", mac=mac))
    if args.get("block"):
        result += "\n" + router.run(_render("block", mac=mac))
    return {"mac": mac, "block": bool(args.get("block")), "output": result[:2000]}


def router_reboot(_args: dict) -> dict:
    try:
        router.run(_render("reboot"))
    except Exception as exc:  # noqa: BLE001 - роутер обрывает соединение при перезагрузке
        log.info("перезагрузка инициирована (%s)", exc)
    if router._client:
        try:
            router._client.close()
        finally:
            router._client = None
    return {"rebooting": True, "at": time.strftime("%H:%M:%S")}


def exec_run(args: dict) -> dict:
    """Произвольная команда на роутере по SSH (только полный режим)."""
    if not FULL_ACCESS:
        raise PermissionError("exec.run доступен только при ACCESS_MODE=full")
    command = str(args["command"]).strip()
    if not command:
        raise ValueError("пустая команда")
    output = router.run(command)
    return {"command": command, "stdout": output[:64_000],
            "truncated": len(output) > 64_000}


def fs_write(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("fs.write доступен только при ACCESS_MODE=full")
    import base64 as _b64
    path = str(args["path"])
    text = str(args["text"])
    op = ">>" if args.get("append") else ">"
    payload = _b64.b64encode(text.encode("utf-8")).decode("ascii")
    router.run(f"mkdir -p \"$(dirname '{path}')\" 2>/dev/null; "
               f"echo '{payload}' | base64 -d {op} '{path}'")
    return {"path": path, "bytes": len(text.encode("utf-8")),
            "mode": "append" if args.get("append") else "overwrite"}


def fs_delete(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("fs.delete доступен только при ACCESS_MODE=full")
    path = str(args["path"])
    flag = "-rf" if args.get("recursive") else "-f"
    router.run(f"rm {flag} '{path}'")
    return {"deleted": path}


def fs_mkdir(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("fs.mkdir доступен только при ACCESS_MODE=full")
    path = str(args["path"])
    router.run(f"mkdir -p '{path}'")
    return {"created": path}


HANDLERS = {
    "net.status": net_status,
    "router.clients": router_clients,
    "router.wifi": router_wifi,
    "router.kick": router_kick,
    "router.reboot": router_reboot,
    "exec.run": exec_run,
    "fs.write": fs_write,
    "fs.delete": fs_delete,
    "fs.mkdir": fs_mkdir,
}


def main() -> None:
    client = AgentClient(
        device_id=os.getenv("ROUTER_DEVICE_ID", "router"),
        name=os.getenv("ROUTER_DEVICE_NAME", "Роутер"),
        platform="router",
        handlers=HANDLERS,
    )
    client.run()


if __name__ == "__main__":
    main()
