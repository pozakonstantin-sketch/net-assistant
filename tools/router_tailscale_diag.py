"""Диагностика Tailscale и exit node на роутере OpenWrt.

Запуск на ВМ (нужен доступ к /opt/net-assistant/.env):

    /opt/net-assistant/.venv/bin/python tools/router_tailscale_diag.py

Скрипт только читает настройки, ничего не меняет.
"""

from __future__ import annotations

import sys
from pathlib import Path

import paramiko

ENV = Path("/opt/net-assistant/.env")

COMMANDS = [
    ("версия", "tailscale version | head -2"),
    ("статус", "tailscale status | head -12"),
    ("exit node и маршруты", "tailscale debug prefs | grep -iE 'AdvertiseRoutes|ExitNode|NoSNAT'"),
    ("адреса", "ip -4 addr show tailscale0 | grep inet; ip -4 addr show br-lan | grep inet"),
    ("маршруты", "ip route | head -8"),
    ("forwarding", "cat /proc/sys/net/ipv4/ip_forward"),
    ("зоны firewall", "uci show firewall | grep -E \"zone\\[[0-9]+\\]\\.(name|network|device|forward|masq)\""),
    ("forwarding-правила", "uci show firewall | grep -E \"forwarding\\[[0-9]+\\]\""),
    ("цепочка forward_ts", "nft list chain inet fw4 forward_ts 2>/dev/null | head -10"),
    ("интернет с роутера", "ping -c 2 -W 3 8.8.8.8 2>&1 | tail -2"),
    ("DNS с роутера", "nslookup ya.ru 2>&1 | head -4"),
]


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    env = read_env()
    host = env.get("ROUTER_HOST", "192.168.1.1")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host,
            port=int(env.get("ROUTER_PORT", "22")),
            username=env.get("ROUTER_USER", "root"),
            password=env.get("ROUTER_PASSWORD", ""),
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"не удалось подключиться к {host}: {exc}")
        return 1

    for title, command in COMMANDS:
        _, stdout, stderr = client.exec_command(command, timeout=30)
        out = stdout.read().decode("utf-8", "replace").strip()
        err = stderr.read().decode("utf-8", "replace").strip()
        print(f"\n### {title}\n{out}")
        if err:
            print(f"[stderr] {err}")

    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
