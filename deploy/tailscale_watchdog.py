#!/usr/bin/env python3
"""Сторож tailscaled для ВМ-ядра.

Домашняя сеть рвёт «простаивающие» TCP-соединения, из-за чего tailscaled теряет
управляющую сессию с координатором (controlplane.tailscale.com) и узел ВМ
показывается в tailnet как offline — телефон не может достучаться до ядра.

Скрипт проверяет состояние Tailscale и при **устойчивой** потере связи
перезапускает tailscaled. Запускается systemd-таймером раз в минуту
(см. deploy/tailscale-watchdog.timer). Работает как root, без зависимостей.

Важные правила (чтобы не навредить):

* сбой должен подтвердиться дважды подряд (``CONFIRM_SECONDS``), иначе
  кратковременное «offline» в момент старта вызывало бы лавину перезапусков;
* пока выпускается TLS-сертификат (``tls-cert-pending``), перезапуск запрещён:
  ACME использует dns-01 и обрывается вместе с процессом — сертификат так и не
  выпускается;
* между перезапусками выдерживается ``COOLDOWN``.
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
COOLDOWN = 120          # не перезапускать чаще, чем раз в 2 минуты
CONFIRM_SECONDS = 90    # сколько сбой должен держаться, чтобы считаться настоящим
TAILSCALE_TIMEOUT = 20

# Тексты health-check, которые означают потерю связи с координатором.
STALE_MARKERS = (
    "hasn't received a network map",
    "Unable to connect to the Tailscale coordination server",
)

# Если идёт выпуск сертификата — не мешать.
CERT_MARKERS = (
    "Fetching TLS certificate",
    "tls-cert-pending",
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
    """Вердикт: ok | self-offline | stale-map | daemon-down."""
    try:
        proc = subprocess.run(
            [TAILSCALE, "status", "--json"],
            capture_output=True, text=True, timeout=TAILSCALE_TIMEOUT,
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


def read_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.loads(fh.read() or "{}")
    except (OSError, ValueError):
        return {}


def write_state(**values) -> None:
    try:
        with open(STATE, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(values))
    except OSError:
        pass


def clear_state() -> None:
    try:
        os.remove(STATE)
    except FileNotFoundError:
        pass


def main() -> int:
    if not shutil.which("tailscale"):
        return 0  # Tailscale не установлен — сторож не нужен

    verdict, detail = check()

    if verdict == "ok":
        clear_state()
        return 0

    if any(marker in detail for marker in CERT_MARKERS):
        log(f"{verdict}: {detail} — выпускается сертификат, не трогаю")
        return 0

    now = time.time()
    state = read_state()
    first = float(state.get("first") or 0)
    last = float(state.get("last_restart") or 0)

    if not first:
        write_state(first=now, last_restart=last)
        log(f"{verdict}: {detail} — сбой замечен, жду {CONFIRM_SECONDS} с")
        return 0

    held = now - first
    if held < CONFIRM_SECONDS:
        log(f"{verdict}: {detail} — держится {int(held)} с, пока не трогаю")
        return 0

    waited = now - last
    if waited < COOLDOWN:
        log(f"{verdict}: {detail} — перезапуск отложен ({int(COOLDOWN - waited)} с)")
        return 0

    log(f"{verdict}: {detail} — сбой держится {int(held)} с, перезапускаю tailscaled")
    result = subprocess.run([SYSTEMCTL, "restart", "tailscaled"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        log(f"ОШИБКА перезапуска (код {result.returncode}): {(result.stderr or '').strip()}")
        return 1

    write_state(first=0, last_restart=now)
    time.sleep(8)
    after, after_detail = check()
    log(f"после перезапуска: {after}" + (f" ({after_detail})" if after != "ok" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
