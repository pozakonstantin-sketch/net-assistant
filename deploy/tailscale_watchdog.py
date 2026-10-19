#!/usr/bin/env python3
"""Сторож tailscaled для ВМ-ядра.

Домашняя сеть рвёт «простаивающие» TCP-соединения примерно за 3 минуты. Из-за
этого tailscaled теряет управляющую сессию с координатором
(controlplane.tailscale.com), перестаёт получать network map и узел ВМ
показывается в tailnet как offline — телефон не может достучаться до ядра.

Скрипт проверяет состояние Tailscale и, если связь потеряна, перезапускает
tailscaled. Запускается systemd-таймером раз в минуту (см.
deploy/tailscale-watchdog.timer). Работает как root, внешних зависимостей нет.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

TAILSCALE = shutil.which("tailscale") or "/usr/bin/tailscale"
SYSTEMCTL = shutil.which("systemctl") or "/bin/systemctl"
STATE = "/run/tailscale-watchdog.state"
LOG = "/var/log/tailscale-watchdog.log"
COOLDOWN = 120  # не перезапускать чаще, чем раз в 2 минуты
TAILSCALE_TIMEOUT = 20

# Тексты health-check, которые означают потерю связи с координатором.
STALE_MARKERS = (
    "hasn't received a network map",
    "Unable to connect to the Tailscale coordination server",
)


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n"
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
    sys.stdout.write(line)


def check() -> tuple[str, str]:
    """Возвращает (вердикт, подробности). Вердикт: ok | self-offline | stale-map | daemon-down."""
    try:
        proc = subprocess.run(
            [TAILSCALE, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=TAILSCALE_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — сторож не должен падать
        return "daemon-down", f"вызов tailscale не удался: {exc}"

    if proc.returncode != 0 or not proc.stdout.strip():
        return "daemon-down", (proc.stderr or "").strip() or f"код {proc.returncode}"

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return "daemon-down", "не разобрать JSON от tailscale"

    self_info = data.get("Self") or {}
    health = " | ".join(data.get("Health") or [])

    if not self_info.get("Online"):
        return "self-offline", health

    for marker in STALE_MARKERS:
        if marker in health:
            return "stale-map", health

    return "ok", health


def read_last_restart() -> float:
    try:
        with open(STATE, encoding="utf-8") as fh:
            return float(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0.0


def main() -> int:
    if not shutil.which("tailscale"):
        return 0  # Tailscale не установлен — сторож не нужен

    verdict, detail = check()

    if verdict == "ok":
        try:
            os.remove(STATE)
        except FileNotFoundError:
            pass
        return 0

    now = time.time()
    last = read_last_restart()
    waited = now - last
    if waited < COOLDOWN:
        log(f"{verdict}: {detail} — перезапуск отложен ({int(COOLDOWN - waited)} с)")
        return 0

    log(f"{verdict}: {detail} — перезапускаю tailscaled")
    result = subprocess.run(
        [SYSTEMCTL, "restart", "tailscaled"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"ОШИБКА перезапуска (код {result.returncode}): {(result.stderr or '').strip()}")
        return 1

    try:
        with open(STATE, "w", encoding="utf-8") as fh:
            fh.write(str(now))
    except OSError:
        pass

    time.sleep(8)
    after, after_detail = check()
    log(f"после перезапуска: {after}" + (f" ({after_detail})" if after != "ok" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
