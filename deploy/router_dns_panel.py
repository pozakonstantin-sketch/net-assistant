#!/usr/bin/env python3
"""Добавляет DNS-запись панели на роутер (OpenWrt/dnsmasq).

Имя из tailnet (например, astra-hub.tailXXXX.ts.net) внутри домашней сети
должно указывать на LAN-адрес ВМ — тогда мини-приложение Telegram открывается
по HTTPS без Tailscale.

Использование: router_dns_panel.py <домен> <LAN-IP>
Реквизиты берутся из /opt/net-assistant/.env (ROUTER_HOST/PORT/USER/PASSWORD).
"""
from __future__ import annotations

import pathlib
import sys

import paramiko

ENV_PATH = pathlib.Path("/opt/net-assistant/.env")


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def run(client: paramiko.SSHClient, command: str) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=30)
    return (stdout.read() + stderr.read()).decode("utf-8", "ignore").strip()


def main() -> int:
    if len(sys.argv) < 3:
        print("использование: router_dns_panel.py <домен> <LAN-IP>", file=sys.stderr)
        return 2
    domain, ip = sys.argv[1], sys.argv[2]

    env = load_env()
    host = env.get("ROUTER_HOST", "192.168.1.1")
    port = int(env.get("ROUTER_PORT", "22"))
    user = env.get("ROUTER_USER", "root")
    password = env.get("ROUTER_PASSWORD", "")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host,
        port=port,
        username=user,
        password=password,
        timeout=15,
        look_for_keys=False,
        allow_agent=False,
    )
    try:
        entry = f"/{domain}/{ip}"
        current = run(client, "uci get dhcp.@dnsmasq[0].address 2>/dev/null")
        if entry in current.split():
            print(f"запись уже есть: {entry}")
            return 0
        run(
            client,
            f'uci add_list "dhcp.@dnsmasq[0].address={entry}"'
            " && uci commit dhcp && /etc/init.d/dnsmasq restart",
        )
        stored = run(client, "uci get dhcp.@dnsmasq[0].address")
        if entry not in stored.split():
            print(f"не удалось добавить запись; сейчас: {stored or '(пусто)'}", file=sys.stderr)
            return 1
        print(f"добавлено: {entry}; dnsmasq перезапущен")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
