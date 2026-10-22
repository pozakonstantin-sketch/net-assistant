"""Карта сети: сбор всего, что видно в домашней сети и в Tailscale.

Один вызов ``net.devices`` возвращает:
    - устройства ассистента (подключённые агенты ядра);
    - LAN-клиенты роутера (DHCP/ARP/WiFi, разбор в router.agent);
    - узлы tailnet'а Tailscale (Tailscaled живёт в той же ВМ).

Частичный отказ не роняет ответ: если роутер или агент ВМ недоступен,
соответствующий раздел будет пустым, а причина — в ``errors``.
"""

from __future__ import annotations

import asyncio
import logging
import time

from . import catalog
from .hub import hub

log = logging.getLogger("network")


class NetworkError(RuntimeError):
    """Сбор карты сети не удался (например, нет ни одного источника)."""


def _assistants() -> list[dict]:
    now = time.time()
    result = []
    for device in hub.devices.values():
        result.append({
            "device_id": device.device_id,
            "name": device.name,
            "platform": device.platform,
            "online": device.online,
            "seen_ago": max(0, int(now - device.last_seen)),
            "actions": catalog.for_device(
                device.platform, device.actions,
                full_access=True) if device.online else [],
        })
    result.sort(key=lambda item: (not item["online"], item["device_id"]))
    return result


def _device_id_for_platform(platform: str) -> str | None:
    for device in hub.devices.values():
        if device.platform == platform:
            return device.device_id
    return None


async def overview(_args: dict) -> dict:
    assistants = _assistants()

    lan: dict = {}
    tailscale: dict = {}
    errors: dict[str, str] = {}

    router_id = _device_id_for_platform("router")
    if router_id:
        try:
            lan = await hub.dispatch(router_id, "router.clients", {}) or {}
        except Exception as exc:  # noqa: BLE001 - отчёт важнее исключения
            log.warning("LAN-клиенты недоступны: %s", exc)
            errors["lan"] = f"{type(exc).__name__}: {exc}"
    else:
        errors["lan"] = "агент роутера не подключён"

    vm_id = _device_id_for_platform("vm-desktop")
    if vm_id:
        try:
            tailscale = await hub.dispatch(vm_id, "tailscale.peers", {}) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("Tailscale-пиры недоступны: %s", exc)
            errors["tailscale"] = f"{type(exc).__name__}: {exc}"
    else:
        errors["tailscale"] = "агент ВМ не подключён"

    lan_clients = lan.get("clients") or []
    ts_peers = tailscale.get("peers") or []
    return {
        "ts": int(time.time()),
        "assistants": assistants,
        "assistants_online": sum(1 for d in assistants if d["online"]),
        "lan": {
            "host": lan.get("host", "router"),
            "count": lan.get("count", len(lan_clients)),
            "wifi": lan.get("wifi", 0),
            "wired": lan.get("wired", 0),
            "clients": lan_clients,
        },
        "tailscale": {
            "self": tailscale.get("self") or {},
            "count": tailscale.get("count", len(ts_peers)),
            "peers": ts_peers,
        },
        "errors": errors,
    }


async def core_action(action: str, args: dict) -> dict:
    """Точка входа для hub.dispatch(device='core')."""
    if action == "net.devices":
        return await overview(args)
    raise ValueError(f"«{action}» — не сетевое действие ядра")


# небольшой хелпер для API-эндпойнта /network
async def snapshot() -> dict:
    return await overview({})
